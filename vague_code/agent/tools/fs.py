"""文件系统工具：read_file / write_file / glob / patch / grep（class-based）。"""

from __future__ import annotations

from pathlib import Path

from vague_code.agent.tools.base import (
    OpType,
    ResourceScope,
    ScopeType,
    Tool,
    ToolExistsError,
    ToolInputError,
    ToolNotFoundError,
    normalize_path,
    pattern_prefix,
)

MAX_GLOB_RESULTS = 1000
MAX_PATCH_BYTES = 1_048_576
MAX_GREP_FILE_SIZE = 5_242_880
MAX_GREP_FILE_COUNT = 500
MAX_GREP_RESULTS = 500

# 搜索工具排除的噪音目录（避免命中构建产物/轨迹日志等）
EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", "runs", ".opencode", ".idea", ".agent",
}


def _path_in_excluded_dir(path: Path, root: Path) -> bool:
    """True when any ancestor directory of `path` is in the exclusion set."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return any(part in EXCLUDED_DIRS for part in rel.parts[:-1])


def _not_found_error(root: Path, path_str: str) -> ToolNotFoundError:
    """文件不存在错误，附带父目录相似名建议（对齐 opencode read 的 Did you mean?）。

    相似度按文件名主体（stem，不含扩展名）双向子串匹配。
    """
    target = (root / path_str).resolve()
    base_stem = target.stem.lower()
    suggestions: list[str] = []
    parent = target.parent
    if parent.is_dir():
        try:
            for child in parent.iterdir():
                if not child.is_file():
                    continue
                child_stem = child.stem.lower()
                if base_stem in child_stem or child_stem in base_stem:
                    try:
                        suggestions.append(str(child.relative_to(root)))
                    except ValueError:
                        continue
                    if len(suggestions) >= 3:
                        break
        except OSError:
            pass
    msg = f"文件未找到: {path_str}"
    if suggestions:
        msg += "\n\n您是不是要找：\n" + "\n".join(suggestions)
    return ToolNotFoundError(msg)


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取文件内容。路径必须相对于工作目录根路径。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作目录根路径的文件路径"},
        },
        "required": ["path"],
    }
    permission = "read"
    op_type = OpType.READ
    scope_type = ScopeType.EXACT

    def scope_path(self, input: dict) -> str:
        return input.get("path", "")

    def run(self, input: dict) -> str:
        path_str = self.extract(input, "path")
        target = self.resolve_path(path_str)
        if not target.is_file():
            raise _not_found_error(self.root, path_str)
        # 预读 = 输出上限 + 1 字节：保证统一截断的字节限可触发且内存安全
        with target.open("rb") as f:
            raw = f.read(self.max_bytes + 1)
        return raw.decode("utf-8-sig", errors="replace")


class WriteFileTool(Tool):
    name = "write_file"
    description = ("写入文件内容（覆盖已存在文件，默认允许覆盖——编辑源码的直接通道）。"
                   "路径必须相对于工作目录根路径。")
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作目录根路径的文件路径"},
            "content": {"type": "string", "description": "要写入文件的内容"},
            "overwrite": {"type": "boolean", "description": "设为 false 时拒绝覆盖已有文件（默认: true）"},
        },
        "required": ["path", "content"],
    }
    permission = "write"
    op_type = OpType.WRITE
    scope_type = ScopeType.EXACT

    def scope_path(self, input: dict) -> str:
        return input.get("path", "")

    def resource_scope(self, input: dict) -> ResourceScope:
        path = self.scope_path(input)
        target = (self.root / path).resolve()
        is_new = not target.exists()
        return ResourceScope(
            path=normalize_path(path),
            scope_type=self.scope_type,
            op_type=OpType.STRUCTURAL_WRITE if is_new else OpType.WRITE,
        )

    def run(self, input: dict) -> str:
        path_str = self.extract(input, "path")
        target = self.resolve_path(path_str)
        content = input.get("content")
        if content is None:
            raise ToolInputError("内容必须是非空字符串，收到 null")
        overwrite = input.get("overwrite", True)
        if target.exists() and not overwrite:
            raise ToolExistsError(f"文件已存在: {path_str}。设置 overwrite=true 覆盖。")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已将 {len(content)} 字符写入 {path_str}"


class GlobTool(Tool):
    name = "glob"
    description = "搜索匹配 glob 模式的文件。支持 * 和 ** 通配符。路径相对于工作目录根路径。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "相对于工作目录根路径的 glob 模式"},
        },
        "required": ["pattern"],
    }
    permission = "read"
    op_type = OpType.READ

    def scope_path(self, input: dict) -> str:
        prefix = pattern_prefix(input.get("pattern", ""))
        return "" if not prefix else prefix

    def resource_scope(self, input: dict) -> ResourceScope:
        prefix = pattern_prefix(input.get("pattern", ""))
        if not prefix:
            # 根级模式（**/*.py、*.py）覆盖整个工作区 → 视为 WORKSPACE
            return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.READ)
        return ResourceScope(path=normalize_path(prefix), scope_type=ScopeType.PREFIX, op_type=OpType.READ)

    def run(self, input: dict) -> str:
        pattern = self.extract(input, "pattern")
        result = []
        for path in self.root.glob(pattern):
            if not path.resolve().is_relative_to(self.root):
                continue
            if _path_in_excluded_dir(path, self.root):
                continue
            result.append(str(path.relative_to(self.root)))
        if len(result) > MAX_GLOB_RESULTS:
            result = result[:MAX_GLOB_RESULTS]
            result.append(f"... 已显示 {MAX_GLOB_RESULTS} 条结果，输出已截断")
        return "\n".join(result)


class PatchTool(Tool):
    name = "patch"
    description = ("对已有文件执行精确字符串替换。将第一次出现的 old_str 替换为 new_str。"
                   "如果 old_str 出现多次则返回错误——请添加更多上下文以使其唯一。")
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作目录根路径的文件路径"},
            "old_str": {"type": "string", "description": "要查找和替换的精确文本"},
            "new_str": {"type": "string", "description": "替换后的文本"},
        },
        "required": ["path", "old_str", "new_str"],
    }
    permission = "write"
    op_type = OpType.WRITE
    scope_type = ScopeType.EXACT

    def scope_path(self, input: dict) -> str:
        return input.get("path", "")

    def run(self, input: dict) -> str:
        path_str = self.extract(input, "path")
        target = self.resolve_path(path_str)
        if not target.is_file():
            raise _not_found_error(self.root, path_str)
        if target.stat().st_size > MAX_PATCH_BYTES:
            raise ToolInputError(
                f"文件过大，无法使用 patch（{target.stat().st_size:_} 字节）。"
                f"最大限制为 {MAX_PATCH_BYTES:_} 字节。请使用 write_file 替换整个文件。"
            )
        old_str = self.extract(input, "old_str")
        new_str = input.get("new_str")
        if new_str is None:
            raise ToolInputError("new_str 必须是字符串，收到 null")
        content = target.read_text(encoding="utf-8-sig")
        count = content.count(old_str)
        if count == 0:
            raise ToolInputError(f"未找到字符串: {old_str}")
        elif count > 1:
            raise ToolInputError(f"发现 {count} 处匹配，请添加更多上下文")
        new_content = content.replace(old_str, new_str, 1)
        target.write_text(new_content, encoding="utf-8")
        return f"已将 {len(new_content)} 字符写入 {path_str}"


class GrepTool(Tool):
    name = "grep"
    description = "在文件内容中搜索正则表达式模式。返回匹配行及其文件路径和行号。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索目录（默认: 工作目录根路径）"},
            "pattern": {"type": "string", "description": "要在文件内容中搜索的正则表达式模式"},
            "include": {"type": "string", "description": "文件过滤 glob 模式（如 '*.py'）"},
        },
        "required": ["pattern"],
    }
    permission = "read"
    op_type = OpType.READ

    def scope_path(self, input: dict) -> str:
        return input.get("path") or ""

    def resource_scope(self, input: dict) -> ResourceScope:
        path = input.get("path") or ""
        if not path:
            # 未指定搜索目录 → 扫整个工作区 → 视为 WORKSPACE
            return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.READ)
        return ResourceScope(path=normalize_path(path), scope_type=ScopeType.PREFIX, op_type=OpType.READ)

    def run(self, input: dict) -> str:
        import re

        pattern = self.extract(input, "pattern")
        path_str = self.extract_optional(input, "path")
        if path_str:
            search_root = self.resolve_path(path_str)
        else:
            search_root = self.root
        include = self.extract_optional(input, "include") or "*"
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return f"正则表达式格式错误: {e}"

        result = []
        file_count = 0
        item_count = 0
        for file in search_root.rglob(include):
            if _path_in_excluded_dir(file, self.root):
                continue
            item_count += 1
            # Safety: stop after scanning too many items (deep directory trees)
            if item_count > 5000:
                result.append("... 已截断于 5000 个目录项")
                break
            if not file.is_file():
                continue
            if file_count >= MAX_GREP_FILE_COUNT:
                result.append(f"... 已截断于 {MAX_GREP_FILE_COUNT} 个文件")
                break
            file_count += 1
            if file.stat().st_size > MAX_GREP_FILE_SIZE:
                continue
            try:
                content = file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(content.splitlines(), start=1):
                if compiled.search(line):
                    rel = str(file.relative_to(self.root))
                    if rel == ".":
                        result.append(f"{i}: {line}")
                    else:
                        result.append(f"{rel}:{i}: {line}")
        if len(result) > MAX_GREP_RESULTS:
            result = result[:MAX_GREP_RESULTS]
            result.append(f"... 已显示 {MAX_GREP_RESULTS} 条结果，输出已截断")
        return "\n".join(result)

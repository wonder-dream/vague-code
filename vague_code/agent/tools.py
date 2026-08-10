from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from vague_code.agent.ir import ToolSpec

DEFAULT_MAX_OVERWRITE = True
MAX_READ_BYTES = 10 * 1024 * 1024
MAX_OUTPUT = 50 * 1024
MAX_GLOB_RESULTS = 1000
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

# bash 工具测试结果结构化（plans/0018）：测试类命令的判定关键词。
# 与 eval/metrics.py 的 TEST_KEYWORDS 同源但独立（产品层不依赖 eval 层）。
TEST_COMMAND_KEYWORDS = ("pytest", "unittest", "nose", "tox", "make test",
                         "run_tests", "run-tests", "nosetests")

# cmd.exe 交互确认提示（del/rmdir 等对目录或通配符参数会弹出确认）。
# stdin 为 DEVNULL 时读到 EOF 视为拒绝，命令静默失败（rc=1）。
INTERACTIVE_CONFIRM_PATTERNS = (
    r"are you sure \(y/n\)",
    r"是否确认\(y/n\)",
    r"确认删除",
    r"confirm.*\(y/n\)",
)


def _is_test_command(command: str) -> bool:
    cmd = (command or "").strip().lower()
    return any(kw in cmd for kw in TEST_COMMAND_KEYWORDS)


def _summarize_test_output(stdout: str, stderr: str, exit_code: int) -> str:
    """从 pytest 风格输出提取结构化 PASS/FAIL 信号。

    规则：`N passed` / `M failed` / `X error`（pytest 汇总行）命中时按数量判
    PASS/FAIL；解析不出时 fallback exit code（0=PASS，非 0=FAIL）。
    返回一行 `[test] PASS (3 passed)` 或 `[test] FAIL (1 failed)`；无法判定
    时返回空串（保持原输出不变）。
    """
    text = stdout + "\n" + stderr
    m = re.search(r"(\d+)\s+passed", text)
    n_passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s+failed", text)
    n_failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+)\s+error", text)
    n_error = int(m.group(1)) if m else 0
    if n_passed or n_failed or n_error:
        if n_failed or n_error:
            detail = []
            if n_failed:
                detail.append(f"{n_failed} failed")
            if n_error:
                detail.append(f"{n_error} error")
            return f"[test] FAIL ({', '.join(detail)})"
        return f"[test] PASS ({n_passed} passed)"
    if exit_code == 0:
        return "[test] PASS (exit 0)"
    return f"[test] FAIL (exit {exit_code})"


def _looks_like_interactive_confirm(stdout: str, exit_code: int) -> bool:
    """True when the command output shows cmd.exe 交互确认提示（stdin 无输入 → 静默拒绝）。"""
    if exit_code == 0 and not re.search(r"退出码|error", stdout, re.I):
        return False
    return any(re.search(p, stdout, re.I) for p in INTERACTIVE_CONFIRM_PATTERNS)


def _interactive_confirm_guidance(command: str, stdout: str, exit_code: int) -> str:
    return (
        f"退出码: {exit_code}\n标准输出:\n{stdout}\n\n"
        f"[交互确认提示] 命令触发了 cmd.exe 的删除确认（stdin 无输入被拒绝）。"
        f"本工具无法应答交互式提示。请改用非交互写法：\n"
        f"  - 删除文件：del /Q <文件>（或 python -c \"import os; os.remove(...)\"）\n"
        f"  - 删除目录：rmdir /S /Q <目录>（或 shutil.rmtree）\n"
        f"  - 注意本环境是 Windows cmd.exe：命令分隔符用 &，不用 ;\n"
    )


@dataclass
class Tool:
    spec: ToolSpec
    factory: Callable[[str], Callable[[dict], str]]

    def bind(self, workdir: str) -> Callable[[dict], str]:
        return self.factory(workdir)


def _read_file_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        path_str = input.get("path", "")
        if path_str is None:
            raise ValueError("路径必须是非空字符串，收到 null")
        if not path_str:
            raise ValueError("需要提供路径")
        if "\x00" in path_str:
            raise ValueError("路径包含空字节")
        target = (root / path_str).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"检测到路径穿越: {path_str}")
        if not target.is_file():
            raise FileNotFoundError(f"文件未找到: {path_str}")
        file_size = target.stat().st_size
        if file_size > MAX_READ_BYTES:
            with target.open("rb") as f:
                raw = f.read(MAX_READ_BYTES)
            content = raw.decode("utf-8-sig", errors="replace")
            return (
                    content
                    + f"\n\n[... 输出截断于 {MAX_READ_BYTES:_} 字节, "
                    + f"文件总大小: {file_size:_} 字节]"
            )
        return target.read_text(encoding="utf-8-sig")

    return handler

def _write_file_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        path_str = input.get("path", "")
        if path_str is None:
            raise ValueError("路径必须是非空字符串，收到 null")
        if not path_str:
            raise ValueError("需要提供路径")
        if "\x00" in path_str:
            raise ValueError("路径包含空字节")
        target = (root / path_str).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"检测到路径穿越: {path_str}")
        overwrite = input.get("overwrite", DEFAULT_MAX_OVERWRITE)
        if target.exists() and not overwrite:
            raise FileExistsError(f"文件已存在: {path_str}。设置 overwrite=true 覆盖。")
        content = input.get("content", "")
        if content is None:
            raise ValueError("内容必须是非空字符串，收到 null")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return f"已将 {len(content)} 字符写入 {path_str}"
    return handler

def _glob_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        pattern = input.get("pattern", "")
        if pattern is None:
            raise ValueError("模式必须是非空字符串，收到 null")
        if not pattern:
            raise ValueError("需要提供模式")
        result = []
        for path in root.glob(pattern):
            if not path.resolve().is_relative_to(root):
                continue
            if _path_in_excluded_dir(path, root):
                continue
            result.append(str(path.relative_to(root)))

        if len(result) > MAX_GLOB_RESULTS:
            result = result[:MAX_GLOB_RESULTS]
            result.append(f"... 已显示 {MAX_GLOB_RESULTS} 条结果，输出已截断")

        return '\n'.join(result)
    return handler

def _patch_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        path_str = input.get("path", "")
        if path_str is None:
            raise ValueError("路径必须是非空字符串，收到 null")
        if not path_str:
            raise ValueError("需要提供路径")
        if "\x00" in path_str:
            raise ValueError("路径包含空字节")
        target = (root / path_str).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"检测到路径穿越: {path_str}")
        if not target.is_file():
            raise FileNotFoundError(f"文件未找到: {path_str}")
        MAX_PATCH_BYTES = 1_048_576
        if target.stat().st_size > MAX_PATCH_BYTES:
            raise ValueError(
                f"文件过大，无法使用 patch（{target.stat().st_size:_} 字节）。"
                f"最大限制为 {MAX_PATCH_BYTES:_} 字节。请使用 write_file 替换整个文件。"
            )
        old_str = input.get("old_str", "")
        if old_str is None:
            raise ValueError("old_str 必须是非空字符串，收到 null")
        if not old_str:
            raise ValueError("需要提供 old_str")
        new_str = input.get("new_str", "")
        if new_str is None:
            raise ValueError("new_str 必须是字符串，收到 null")
        content = target.read_text(encoding="utf-8-sig")
        count = content.count(old_str)
        if count == 0:
            raise ValueError(f"未找到字符串: {old_str}")
        elif count > 1:
            raise ValueError(f"发现 {count} 处匹配，请添加更多上下文")
        else:
            new_content = content.replace(old_str, new_str, 1)
        target.write_text(new_content, encoding="utf-8")
        return f"已将 {len(new_content)} 字符写入 {path_str}"
    return handler

def _grep_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        pattern = input.get("pattern")
        if pattern is None:
            raise ValueError("模式必须是字符串，收到 null")
        if not pattern:
            raise ValueError("模式必须是非空字符串")
        path_str = input.get("path")
        if path_str is None:
            path_str = ""
        if "\x00" in path_str:
            raise ValueError("路径包含空字节")
        if not path_str:
            search_root = root
        else:
            search_root = (root / path_str).resolve()
        if not search_root.is_relative_to(root):
            raise PermissionError(f"检测到路径穿越: {path_str}")
        include = input.get("include")
        if include is None:
            include = "*"
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return f"正则表达式格式错误: {e}"

        result = []
        file_count = 0
        item_count = 0
        for file in search_root.rglob(include):
            if _path_in_excluded_dir(file, root):
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
                    rel = str(file.relative_to(root))
                    if rel == ".":
                        result.append(f"{i}: {line}")
                    else:
                        result.append(f"{rel}:{i}: {line}")
        if len(result) > MAX_GREP_RESULTS:
            result = result[:MAX_GREP_RESULTS]
            result.append(f"... 已显示 {MAX_GREP_RESULTS} 条结果，输出已截断")
        return "\n".join(result)
    return handler

def _bash_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    _MULTILINE_PY_C = re.compile(r'python\s+-c\s+"([\s\S]*?)"')

    def _rewrite_multiline_python(command: str) -> tuple[Path, str] | None:
        """Rewrite `python -c "multi-line code"` to a temp script file.

        cmd.exe corrupts -c arguments containing newlines (rc=0, no output);
        running the code from a .py file preserves newlines. Returns
        (script_path, rewritten_command), or None when not affected.
        """
        if "\n" not in command:
            return None
        match = _MULTILINE_PY_C.search(command)
        if match is None:
            return None
        code = match.group(1)
        if "\n" not in code:
            return None
        script = Path(tempfile.gettempdir()) / f"vaguecode_{uuid.uuid4().hex[:8]}.py"
        try:
            script.write_text(code, encoding="utf-8")
        except OSError:
            return None
        rewritten = command[: match.start()] + f'python "{script}"' + command[match.end():]
        return script, rewritten

    def handler(input: dict) -> str:
        command = input.get("command", "")
        if command is None:
            raise ValueError("命令必须是非空字符串，收到 null")
        if not command:
            raise ValueError("需要提供命令")
        cwd_str = input.get("cwd")
        if cwd_str:
            cwd_path = (root / cwd_str).resolve()
            if not cwd_path.is_relative_to(root):
                raise PermissionError(f"Path traversal detected: {cwd_str}")
        else:
            cwd_path = root
        temp_script: Path | None = None
        rewritten = _rewrite_multiline_python(command)
        if rewritten is not None:
            temp_script, command = rewritten
        if os.name == "nt" and not command.strip().lower().startswith("chcp"):
            command = f"chcp 65001 >nul & {command}"
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired as exc:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True, timeout=5,
                    )
                proc.kill()
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    stdout_bytes = exc.stdout or b""
                    stderr_bytes = exc.stderr or b""
                stdout_partial = stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT]
                stderr_partial = stderr_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT]
                raise RuntimeError(
                    f"命令在 30 秒后超时\n"
                    f"部分标准输出:\n{stdout_partial}\n"
                    f"部分标准错误输出:\n{stderr_partial}"
                )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            if len(stdout) > MAX_OUTPUT:
                stdout = stdout[:MAX_OUTPUT] + f"\n\n[... 标准输出截断于 {MAX_OUTPUT:_} 字节]"
            if len(stderr) > MAX_OUTPUT:
                stderr = stderr[:MAX_OUTPUT] + f"\n\n[... 标准错误输出截断于 {MAX_OUTPUT:_} 字节]"
            # 测试结果结构化（plans/0018 #5）：测试类命令追加 PASS/FAIL 行，给模型明确信号
            if _is_test_command(command):
                verdict = _summarize_test_output(stdout, stderr, proc.returncode)
                if verdict:
                    return (f"退出码: {proc.returncode}\n标准输出:\n{stdout}\n"
                            f"标准错误输出:\n{stderr}\n\n{verdict}")
            if _looks_like_interactive_confirm(stdout, proc.returncode):
                return _interactive_confirm_guidance(command, stdout, proc.returncode)
            return f"退出码: {proc.returncode}\n标准输出:\n{stdout}\n标准错误输出:\n{stderr}"
        finally:
            if temp_script is not None:
                try:
                    temp_script.unlink(missing_ok=True)
                except OSError:
                    pass
    return handler

READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="读取文件内容。路径必须相对于工作目录根路径。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作目录根路径的文件路径"},
        },
        "required": ["path"],
    },
)

WRITE_FILE_SPEC = ToolSpec(
    name="write_file",
    description="写入文件内容（覆盖已存在文件，默认允许覆盖——编辑源码的直接通道）。路径必须相对于工作目录根路径。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作目录根路径的文件路径"},
            "content": {"type": "string", "description": "要写入文件的内容"},
            "overwrite": {"type": "boolean", "description": "设为 false 时拒绝覆盖已有文件（默认: true）"},
        },
        "required": ["path", "content"],
    },
)

GLOB_SPEC = ToolSpec(
    name="glob",
    description="搜索匹配 glob 模式的文件。支持 * 和 ** 通配符。路径相对于工作目录根路径。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "相对于工作目录根路径的 glob 模式"},
        },
        "required": ["pattern"],
    },
)

PATCH_SPEC = ToolSpec(
    name="patch",
    description="对已有文件执行精确字符串替换。将第一次出现的 old_str 替换为 new_str。如果 old_str 出现多次则返回错误——请添加更多上下文以使其唯一。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对于工作目录根路径的文件路径"},
            "old_str": {"type": "string", "description": "要查找和替换的精确文本"},
            "new_str": {"type": "string", "description": "替换后的文本"},
        },
        "required": ["path", "old_str", "new_str"],
    },
)

GREP_SPEC = ToolSpec(
    name="grep",
    description="在文件内容中搜索正则表达式模式。返回匹配行及其文件路径和行号。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "搜索目录（默认: 工作目录根路径）"},
            "pattern": {"type": "string", "description": "要在文件内容中搜索的正则表达式模式"},
            "include": {"type": "string", "description": "文件过滤 glob 模式（如 '*.py'）"},
        },
        "required": ["pattern"],
    },
)

BASH_SPEC = ToolSpec(
    name="bash",
    description="执行 shell 命令并返回其输出。分别返回标准输出和标准错误输出。"
                "注意：本环境是 Windows，实际使用 cmd.exe（非 bash）。"
                "多命令分隔符用 &（不用 ;）；删除文件用 del /Q 或 python os.remove，"
                "删除目录用 rmdir /S /Q，避免交互确认提示导致失败。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "cwd": {"type": "string", "description": "命令的工作目录（默认: 工作目录根路径）"},
        },
        "required": ["command"],
    },
)

CODE_SEARCH_SPEC = ToolSpec(
    name="code_search",
    description="在工作区代码库中按符号名（函数/类/方法）搜索定义位置。"
                "返回 file:line: signature 列表。当需要定位某个函数或类在哪里定义时使用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要搜索的符号名或正则表达式"},
            "path": {"type": "string", "description": "过滤文件路径（可选）"},
        },
        "required": ["query"],
    },
)


def make_code_search_handler(repo_index):
    def handler(input: dict) -> str:
        query = input.get("query", "")
        if not query:
            return "需要提供搜索查询内容。"
        path = input.get("path") or None
        results = repo_index.search(query, k=20, path=path)
        if not results:
            return f"未找到与 {query!r} 匹配的符号。"
        lines = [f"{s.file}:{s.line}: {s.signature}" for s in results]
        if len(lines) > MAX_GREP_RESULTS:
            lines = lines[:MAX_GREP_RESULTS]
            lines.append(f"... 已显示 {MAX_GREP_RESULTS} 条结果，输出已截断")
        return "\n".join(lines)
    return handler


DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": Tool(spec=READ_FILE_SPEC, factory=_read_file_factory),
    "write_file": Tool(spec=WRITE_FILE_SPEC, factory=_write_file_factory),
    "glob": Tool(spec=GLOB_SPEC, factory=_glob_factory),
    "patch": Tool(spec=PATCH_SPEC, factory=_patch_factory),
    "grep": Tool(spec=GREP_SPEC, factory=_grep_factory),
    "bash": Tool(spec=BASH_SPEC, factory=_bash_factory),
}

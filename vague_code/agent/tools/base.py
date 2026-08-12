"""工具抽象层（ADR-0004 重构，2026-08-12）。

class-based 工具定义：元数据声明（permission / op_type / scope_type）+ 模板方法
（参数提取 → 路径安全 → run → 统一截断 → ToolResult）。对齐调研结论：

- opencode：Tool.Def + InvalidArgumentsError（message 为给模型的修正指引）+ ExecuteResult 结构化输出
- Codex：FunctionCallError{RespondToModel | Fatal} 两态错误
- PI：truncate.ts 统一截断（2000 行 / 50KB，结构化统计）
- 权限为横切关注点（三家均在 executor 层判定）——工具以 permission_class 元数据声明，
  由 permission 层消费，替代按工具名硬编码分支
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar

from vague_code.agent.ir import ToolSpec
from vague_code.agent.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_output


# ── 两态错误契约（对齐 Codex RespondToModel | Fatal）─────────────────────────

class ToolError(Exception):
    """工具错误基类：message 是回喂模型的修正指引（opencode InvalidArgumentsError 范式）。

    未捕获的其他异常 = 致命错误（loop 层转 is_error 回喂，语义不变）。
    多继承内置异常 → 既有 pytest.raises(内置异常) 断言兼容。
    """


class ToolInputError(ToolError, ValueError):
    """参数校验错误。"""


class ToolPathError(ToolError, PermissionError):
    """路径穿越等安全错误。"""


class ToolNotFoundError(ToolError, FileNotFoundError):
    """文件/路径不存在（message 含 Did you mean? 建议）。"""


class ToolExistsError(ToolError, FileExistsError):
    """目标已存在且不允许覆盖。"""


class ToolExecutionError(ToolError, RuntimeError):
    """执行错误（bash 超时等）。"""


# ── 资源模型（并发调度消费；concurrency 从本模块导入）────────────────────────

class OpType(Enum):
    READ = "R"
    WRITE = "W"
    STRUCTURAL_WRITE = "SW"


class ScopeType(Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    WORKSPACE = "workspace"


@dataclass
class ResourceScope:
    path: str
    scope_type: ScopeType
    op_type: OpType


def normalize_path(path: str) -> str:
    """Windows 文件系统大小写不敏感：归一化为小写（并发冲突判定的基础）。"""
    import os
    p = path.replace("\\", "/")
    return p.lower() if os.name == "nt" else p


def pattern_prefix(pattern: str) -> str:
    """Extract the directory prefix from a glob pattern before the first wildcard."""
    import re
    p = pattern.replace("\\", "/")
    trimmed = p.rstrip("/")
    m = re.search(r"[*?[\]]", trimmed)
    if m:
        prefix = trimmed[:m.start()]
        if "/" in prefix:
            prefix = prefix.rsplit("/", 1)[0]
        else:
            return ""
        return prefix or ""
    return trimmed or ""


# ── 结构化输出（对齐 opencode ExecuteResult{output, metadata}）──────────────

@dataclass
class ToolResult:
    """工具执行结果：output = 模型可见文本；metadata = 结构信息（截断统计等）。"""
    output: str
    metadata: dict = field(default_factory=dict)


# ── Tool ABC ────────────────────────────────────────────────────────────────

class Tool(ABC):
    """工具基类：元数据类变量声明 + 模板方法 handle()。

    handle() 统一流程：run()（子类核心逻辑）→ truncate_output()（统一截断）→ ToolResult。
    参数提取 extract() 与路径安全 resolve_path() 由基类提供（对齐 PI prepareArguments）。
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict] = {}
    # 权限分类（permission 层消费）：read / write / bash_safe / bash_dangerous / network
    permission: ClassVar[str] = "write"
    # 并发资源元数据（concurrency 层消费）
    op_type: ClassVar[OpType] = OpType.WRITE
    scope_type: ClassVar[ScopeType] = ScopeType.WORKSPACE
    # 统一截断上限（默认业界参数 2000 行 / 50KB）
    max_lines: ClassVar[int] = DEFAULT_MAX_LINES
    max_bytes: ClassVar[int] = DEFAULT_MAX_BYTES

    def __init__(self, workdir: str):
        self.root = Path(workdir).resolve()

    @classmethod
    def spec(cls) -> ToolSpec:
        return ToolSpec(name=cls.name, description=cls.description, parameters=cls.parameters)

    @classmethod
    def bind(cls, workdir: str) -> "Tool":
        """绑定工作目录返回新实例（workdir 在一次 run 内不变，绑定一次）。"""
        return cls(workdir)

    def __call__(self, input: dict) -> ToolResult:
        return self.handle(input)

    def handle(self, input: dict) -> ToolResult:
        """模板方法：run → 统一截断 → ToolResult（截断统计入 metadata）。

        截断发生时调用 on_truncated(full, tr) hook（如 bash 落盘完整输出）。
        """
        out = self.run(input)
        tr = truncate_output(out, self.max_lines, self.max_bytes)
        metadata: dict = {
            "title": self.name,
            "truncated": tr.truncated,
            "truncated_by": tr.truncated_by,
            "output_lines": tr.output_lines,
            "output_bytes": tr.output_bytes,
            "total_lines": tr.total_lines,
            "total_bytes": tr.total_bytes,
        }
        if tr.truncated:
            metadata.update(self.on_truncated(out, tr))
        return ToolResult(output=tr.content, metadata=metadata)

    def on_truncated(self, full_output: str, tr) -> dict:
        """输出被截断时的钩子（默认无操作）；返回追加进 metadata 的字段。"""
        return {}

    @abstractmethod
    def run(self, input: dict) -> str:
        """子类实现核心逻辑；错误抛 ToolError 子类或内置异常。"""

    # ── 参数提取基类（None / 空 / 类型校验）──────────────────────────────

    def extract(self, input: dict, key: str, *, required: bool = True) -> str:
        value = input.get(key)
        if value is None:
            raise ToolInputError(f"参数 {key} 必须是非空字符串，收到 null")
        if required and not str(value).strip():
            raise ToolInputError(f"需要提供 {key}")
        return str(value)

    def extract_optional(self, input: dict, key: str) -> str:
        value = input.get(key)
        return str(value) if value is not None else ""

    # ── 路径安全基类（空字节 + 穿越防护）─────────────────────────────────

    def resolve_path(self, path_str: str) -> Path:
        if "\x00" in path_str:
            raise ToolInputError("路径包含空字节")
        target = (self.root / path_str).resolve()
        if not target.is_relative_to(self.root):
            raise ToolPathError(f"检测到路径穿越: {path_str}")
        return target

    # ── 权限 / 并发元数据（默认实现，子类按需覆写）──────────────────────

    def permission_class(self, input: dict) -> str:
        return self.permission

    def scope_path(self, input: dict) -> str:
        """并发 scope 的路径提取（默认空 = WORKSPACE）。"""
        return ""

    def resource_scope(self, input: dict) -> ResourceScope:
        return ResourceScope(
            path=normalize_path(self.scope_path(input)),
            scope_type=self.scope_type,
            op_type=self.op_type,
        )

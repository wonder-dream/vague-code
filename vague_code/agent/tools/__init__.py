"""工具注册表（ADR-0004）：class-based 工具定义。

DEFAULT_TOOLS 存工具类，Agent 通过 `bind_tools(workdir)` 实例化（key == name 校验）。
动态工具（code_search）由 loop 按需实例化注入。
"""

from __future__ import annotations

from vague_code.agent.tools.bash_tool import (
    BashTool,
    _is_test_command,
    _summarize_test_output,
)
from vague_code.agent.tools.base import (
    OpType,
    ResourceScope,
    ScopeType,
    Tool,
    ToolError,
    ToolExecutionError,
    ToolInputError,
    ToolNotFoundError,
    ToolPathError,
    ToolResult,
)
from vague_code.agent.tools.fs import (
    GlobTool,
    GrepTool,
    PatchTool,
    ReadFileTool,
    WriteFileTool,
)

DEFAULT_TOOLS: dict[str, type[Tool]] = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "glob": GlobTool,
    "patch": PatchTool,
    "grep": GrepTool,
    "bash": BashTool,
}


def bind_tools(
    registry: dict[str, type[Tool]], workdir: str,
) -> dict[str, Tool]:
    """实例化注册表：workdir 绑定一次，key == name 校验（fail-fast）。"""
    bound: dict[str, Tool] = {}
    for key, tool_cls in registry.items():
        if key != tool_cls.name:
            raise ValueError(f"Registry key '{key}' does not match tool name '{tool_cls.name}'")
        bound[key] = tool_cls(workdir)
    return bound


__all__ = [
    "DEFAULT_TOOLS",
    "bind_tools",
    "Tool",
    "ToolResult",
    "ToolError",
    "ToolInputError",
    "ToolPathError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "OpType",
    "ScopeType",
    "ResourceScope",
    "BashTool",
    "ReadFileTool",
    "WriteFileTool",
    "GlobTool",
    "PatchTool",
    "GrepTool",
    "_is_test_command",
    "_summarize_test_output",
]

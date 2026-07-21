from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.agent.ir import ToolSpec

MAX_READ_BYTES = 10 * 1024 * 1024


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
            raise ValueError("path must be a non-empty string, got null")
        if not path_str:
            raise ValueError("path is required")
        if "\x00" in path_str:
            raise ValueError("path contains null byte")
        target = (root / path_str).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"Path traversal detected: {path_str}")
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path_str}")
        file_size = target.stat().st_size
        if file_size > MAX_READ_BYTES:
            content = target.read_text(encoding="utf-8-sig")[:MAX_READ_BYTES]
            return (
                content
                + f"\n\n[... output truncated at {MAX_READ_BYTES:_} bytes, "
                + f"total file size: {file_size:_} bytes]"
            )
        return target.read_text(encoding="utf-8-sig")

    return handler


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="Read the contents of a file. The path must be relative to the workspace root.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace root"},
        },
        "required": ["path"],
    },
)

DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": Tool(spec=READ_FILE_SPEC, factory=_read_file_factory),
}

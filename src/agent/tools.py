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

def _write_file_factory(workdir: str) -> Callable[[dict], str]:
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
        content = input.get("content", "")
        if content is None:
            raise ValueError("content must be a non-empty string, got null")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return f"Wrote {len(content)} bytes to {path_str}"
    return handler

def _glob_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        pattern = input.get("pattern", "")
        if pattern is None:
            raise ValueError("pattern must be a non-empty string, got null")
        if not pattern:
            raise ValueError("pattern is required")
        target = root.glob(pattern)
        result = []
        for path in target:
            result.append(str(path))

        return '\n'.join(result)
    return handler

def _patch_factory(workdir: str) -> Callable[[dict], str]:
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
        old_str = input.get("old_str", "")
        if old_str is None:
            raise ValueError("old_str must be a non-empty string, got null")
        if not old_str:
            raise ValueError("old_str is required")
        new_str = input.get("new_str", "")
        if new_str is None:
            raise ValueError("new_str must be a string, got null")
        content = target.read_text(encoding="utf-8-sig")
        count = content.count(old_str)
        if count == 0:
            raise ValueError(f"String not found: {old_str}")
        elif count > 1:
            raise ValueError(f"found {count} occurrences, add more context")
        else:
            new_content = content.replace(old_str, new_str, 1)
        target.write_text(new_content, encoding="utf-8")
        return f"Wrote {len(new_content)} bytes to {path_str}"
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

WRITE_FILE_SPEC = ToolSpec(
    name="write_file",
    description="write the contents of a file. The path must be relative to the workspace root.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace root"},
            "content": {"type": "string", "description": "Content to write to the file"},
        },
        "required": ["path", "content"],
    },
)

GLOB_SPEC = ToolSpec(
    name="glob",
    description="Find files matching a glob pattern. Supports * and ** wildcards. The pattern is relative to the workspace root.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern relative to workspace root"},
        },
        "required": ["pattern"],
    },
)

PATCH_SPEC = ToolSpec(
    name="patch",
    description="Performs exact string replacements in an existing file. Replaces the first occurrence of old_str with new_str. Returns an error if old_str is found multiple times — add more surrounding context to make it unique.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace root"},
            "old_str": {"type": "string", "description": "The exact text to find and replace"},
            "new_str": {"type": "string", "description": "The text to replace it with"},
        },
        "required": ["path", "old_str", "new_str"],
    },
)

DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": Tool(spec=READ_FILE_SPEC, factory=_read_file_factory),
    "write_file": Tool(spec=WRITE_FILE_SPEC, factory=_write_file_factory),
    "glob": Tool(spec=GLOB_SPEC, factory=_glob_factory),
    "patch": Tool(spec=PATCH_SPEC, factory=_patch_factory),
}

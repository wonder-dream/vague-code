from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import re

from src.agent.ir import ToolSpec

DEFAULT_MAX_OVERWRITE = False
MAX_READ_BYTES = 10 * 1024 * 1024
MAX_OUTPUT = 50 * 1024
MAX_GLOB_RESULTS = 1000
MAX_GREP_RESULTS = 500

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
            with target.open("rb") as f:
                raw = f.read(MAX_READ_BYTES)
            content = raw.decode("utf-8-sig", errors="replace")
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
        overwrite = input.get("overwrite", DEFAULT_MAX_OVERWRITE)
        if target.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path_str}. Set overwrite=true to replace it.")
        content = input.get("content", "")
        if content is None:
            raise ValueError("content must be a non-empty string, got null")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return f"Wrote {len(content.encode("utf-8"))} bytes to {path_str}"
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
            result.append(str(path.relative_to(root)))

        if len(result) > MAX_GLOB_RESULTS:
            result = result[:MAX_GLOB_RESULTS]
            result.append(f"... {MAX_GLOB_RESULTS} results shown, output truncated")

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
        MAX_PATCH_BYTES = 1_048_576
        if target.stat().st_size > MAX_PATCH_BYTES:
            raise ValueError(
                f"File too large for patch ({target.stat().st_size:_} bytes). "
                f"Maximum is {MAX_PATCH_BYTES:_} bytes. Use write_file to replace the entire file instead."
            )
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
        return f"Wrote {len(new_content.encode("utf-8"))} bytes to {path_str}"
    return handler

def _grep_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        pattern = input.get("pattern")
        if pattern is None:
            raise ValueError("pattern must be a string, got null")
        if not pattern:
            raise ValueError("pattern must be a non-empty string")
        path_str = input.get("path", "")
        if "\x00" in path_str:
            raise ValueError("path contains null byte")
        if not path_str:
            search_root = root
        else:
            search_root = (root / path_str).resolve()
        if not search_root.is_relative_to(root):
            raise PermissionError(f"Path traversal detected: {path_str}")
        include = input.get("include")
        if include is None:
            include = "*"
        result = []
        for file in search_root.rglob(include):
            if file.is_file():
                try:
                    content = file.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                try:
                    compiled = re.compile(pattern)
                except re.error:
                    continue
                for i, line in enumerate(content.splitlines(), start=1):
                    if compiled.search(line):
                        result.append(f"{file}:{i}: {line}")
        if len(result) > MAX_GREP_RESULTS:
            result = result[:MAX_GREP_RESULTS]
            result.append(f"... {MAX_GREP_RESULTS} results shown, output truncated")
        return "\n".join(result)
    return handler

def _bash_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()
    def handler(input: dict) -> str:
        command = input.get("command", "")
        if command is None:
            raise ValueError("command must be a non-empty string, got null")
        if not command:
            raise ValueError("command is required")
        cwd_str = input.get("cwd")
        if cwd_str:
            cwd_path = (root / cwd_str).resolve()
            if not cwd_path.is_relative_to(root):
                raise PermissionError(f"Path traversal detected: {cwd_str}")
        else:
            cwd_path = root
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd_path,
                capture_output=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
                )
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired:
            raise RuntimeError("command timed out after 30 seconds")
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + f"\n\n[... stdout truncated at {MAX_OUTPUT:_} bytes]"
        if len(stderr) > MAX_OUTPUT:
            stderr = stderr[:MAX_OUTPUT] + f"\n\n[... stderr truncated at {MAX_OUTPUT:_} bytes]"
        return f"exit code: {result.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
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
            "overwrite": {"type": "boolean", "description": "Set to true to overwrite an existing file (default: false)"},
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

GREP_SPEC = ToolSpec(
    name="grep",
    description="Search for a regex pattern in file contents. Returns matching lines with file path and line number.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to search (default: workspace root)"},
            "pattern": {"type": "string", "description": "The regex pattern to search for in file contents"},
            "include": {"type": "string", "description": "File glob pattern to filter files (e.g. '*.py')"},
        },
        "required": ["pattern"],
    },
)

BASH_SPEC = ToolSpec(
    name="bash",
    description="Execute a shell command and return its output. Returns stdout and stderr separately.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "cwd": {"type": "string", "description": "Working directory for the command (default: workspace root)"},
        },
        "required": ["command"],
    },
)

DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": Tool(spec=READ_FILE_SPEC, factory=_read_file_factory),
    "write_file": Tool(spec=WRITE_FILE_SPEC, factory=_write_file_factory),
    "glob": Tool(spec=GLOB_SPEC, factory=_glob_factory),
    "patch": Tool(spec=PATCH_SPEC, factory=_patch_factory),
    "grep": Tool(spec=GREP_SPEC, factory=_grep_factory),
    "bash": Tool(spec=BASH_SPEC, factory=_bash_factory),
}

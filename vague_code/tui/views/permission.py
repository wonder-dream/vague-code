"""Structured permission dialog content helpers for the vague-code TUI.

Turns a raw tool Operation into a readable dialog: per-tool titles,
accent colors, and structured (label, value) rows — no raw `str(input)`
dumps. Kept as pure functions so the dialog stays dumb and testable.
"""

from __future__ import annotations

from rich.text import Text

from vague_code.agent.permission import Operation

_TOOL_ACCENTS = {
    "bash": "#b28443",
    "write_file": "#c85f5f",
    "patch": "#c85f5f",
    "read_file": "#50b7c2",
    "glob": "#50b7c2",
    "grep": "#50b7c2",
    "code_search": "#50b7c2",
}

_TOOL_TITLES = {
    "bash": "Run command",
    "write_file": "Write file",
    "patch": "Apply patch",
    "read_file": "Read file",
    "glob": "List files",
    "grep": "Search files",
    "code_search": "Search code",
}

_TOOL_GLYPHS = {
    "bash": "$",
    "write_file": "✎",
    "patch": "✎",
    "read_file": "≡",
    "glob": "⌕",
    "grep": "⌕",
    "code_search": "⌕",
}


def tool_accent(tool_name: str) -> str:
    return _TOOL_ACCENTS.get(tool_name, "#b28443")


def permission_title(tool_name: str) -> str:
    return _TOOL_TITLES.get(tool_name, "Permission required")


def tool_glyph(tool_name: str) -> str:
    return _TOOL_GLYPHS.get(tool_name, "!")


def operation_rows(op: Operation) -> list[tuple[str, str]]:
    """Structured (label, value) rows for the operation body."""
    tool = op.tool_name
    data = op.input or {}
    rows: list[tuple[str, str]] = []
    if tool == "bash":
        rows.append(("command", op.command or str(data.get("command", ""))))
    elif tool in ("write_file", "read_file"):
        rows.append(("path", str(data.get("path", ""))))
        content = data.get("content")
        if tool == "write_file" and isinstance(content, str):
            rows.append(("content", _preview(content)))
    elif tool == "patch":
        rows.append(("path", str(data.get("path", ""))))
        old_str = data.get("old_str")
        new_str = data.get("new_str")
        if isinstance(old_str, str):
            rows.append(("old", _preview(old_str)))
        if isinstance(new_str, str):
            rows.append(("new", _preview(new_str)))
    elif tool in ("glob", "grep"):
        rows.append(("pattern", str(data.get("pattern", ""))))
        if data.get("path"):
            rows.append(("path", str(data["path"])))
    else:
        for key, value in data.items():
            rows.append((str(key), _preview(str(value))))
    return rows


def render_operation_body(op: Operation) -> Text:
    """Render the operation body: a shell-style line for bash, rows otherwise."""
    rendered = Text()
    rows = operation_rows(op)
    if op.tool_name == "bash" and rows:
        rendered.append("$ ", style=f"{tool_accent(op.tool_name)} bold")
        rendered.append(rows[0][1], style="#cfd1d6")
    else:
        for label, value in rows:
            rendered.append(f"  {label}: ", style="#6e6d72")
            rendered.append(value, style="#cfd1d6")
            rendered.append("\n")
    return rendered


def _preview(text: str, limit: int = 300) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."

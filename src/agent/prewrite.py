"""Prewrite diff computation for write tools (permission review support).

Pure functions: given a write/patch tool input, compute a bounded unified diff
between the current file content and the proposed content. Used by the loop to
attach `Operation.review` before asking the user, and rendered by the TUI.
"""

from __future__ import annotations

import difflib
from pathlib import Path

REVIEW_TOOLS = ("write_file", "patch")
MAX_DIFF_LINES = 400


def compute_prewrite_review(tool_name: str, input_: dict, workdir: str) -> dict | None:
    """Return a review payload, or None when the tool is not reviewable."""
    if tool_name not in REVIEW_TOOLS:
        return None
    path_str = input_.get("path") or ""
    if not path_str or "\x00" in path_str:
        return None
    root = Path(workdir).resolve()
    try:
        target = (root / path_str).resolve()
        if not target.is_relative_to(root):
            return None
    except (OSError, ValueError):
        return None

    old_text = target.read_text(encoding="utf-8-sig") if target.is_file() else ""
    new_text = _proposed_text(tool_name, input_, old_text)
    if new_text is None:
        return {"files": [], "summary": {"added_lines": 0, "removed_lines": 0}, "error": "无法预览此写入"}
    if old_text == new_text:
        return {"files": [], "summary": {"added_lines": 0, "removed_lines": 0}}

    operation = "CREATE" if not target.exists() else "MODIFY"
    diff = _unified_diff(path_str, old_text, new_text)
    added = _count_lines(diff, "+")
    removed = _count_lines(diff, "-")
    return {
        "files": [
            {
                "path": path_str,
                "operation": operation,
                "added_lines": added,
                "removed_lines": removed,
                "diff": diff,
            }
        ],
        "summary": {"added_lines": added, "removed_lines": removed},
    }


def _proposed_text(tool_name: str, input_: dict, old_text: str) -> str | None:
    if tool_name == "write_file":
        content = input_.get("content")
        return content if isinstance(content, str) else None
    if tool_name == "patch":
        old_str = input_.get("old_str")
        new_str = input_.get("new_str")
        if not isinstance(old_str, str) or not isinstance(new_str, str):
            return None
        index = old_text.find(old_str)
        if index < 0:
            return None
        return old_text[:index] + new_str + old_text[index + len(old_str) :]
    return None


def _unified_diff(path: str, old: str, new: str) -> str:
    diff = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    return "\n".join(diff[:MAX_DIFF_LINES])


def _count_lines(diff: str, prefix: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if line.startswith(prefix) and not line.startswith(prefix * 3)
    )

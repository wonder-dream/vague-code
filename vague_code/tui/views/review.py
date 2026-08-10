"""Rich rendering for pre-write review payloads.

Ported from the firstcoder TUI reference (app/review_view.py): a bounded
red/green unified-diff card shown inside the permission dialog.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

DEFAULT_MAX_DIFF_LINES_PER_FILE = 80


def render_prewrite_review(
    payload: dict[str, Any],
    *,
    max_diff_lines_per_file: int = DEFAULT_MAX_DIFF_LINES_PER_FILE,
) -> Text:
    files = [item for item in payload.get("files", []) if isinstance(item, dict)]
    summary_raw = payload.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    added = _int(summary.get("added_lines"))
    removed = _int(summary.get("removed_lines"))
    rendered = Text()
    rendered.append(
        f"Review before writing · {_file_label(len(files))} · +{added} -{removed}",
        style="#b28443 bold",
    )
    error = str(payload.get("error") or "")
    if error:
        rendered.append(f"\nPreview unavailable: {error}", style="#c85f5f bold")
        return rendered
    if not files:
        rendered.append("\nNo text-file changes were produced.", style="#808185")
        return rendered

    for index, item in enumerate(files):
        path = str(item.get("path") or "unknown")
        operation = str(item.get("operation") or "modify").upper()
        item_added = _int(item.get("added_lines"))
        item_removed = _int(item.get("removed_lines"))
        rendered.append("\n\n")
        rendered.append(f"{operation:<7} ", style=_operation_style(operation))
        rendered.append(path, style="#cfd1d6 bold")
        rendered.append(f" · +{item_added} -{item_removed}", style="#808185")
        _append_diff(rendered, str(item.get("diff") or ""), max_lines=max_diff_lines_per_file)
    return rendered


def _append_diff(rendered: Text, diff: str, *, max_lines: int) -> None:
    lines = diff.splitlines()
    if not lines:
        rendered.append("\n(no textual diff)", style="#808185")
        return
    visible = lines[: max(0, max_lines)]
    for line in visible:
        rendered.append("\n  ")
        rendered.append(line, style=_diff_line_style(line))
    hidden = len(lines) - len(visible)
    if hidden:
        rendered.append(f"\n  … {hidden} diff lines hidden", style="#808185")


def _diff_line_style(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return "#7bba55"
    if line.startswith("-") and not line.startswith("---"):
        return "#c85f5f"
    if line.startswith("@@"):
        return "#5fb5ff"
    if line.startswith(("+++", "---")):
        return "#808185"
    return "#cfd1d6"


def _operation_style(operation: str) -> str:
    if operation in {"CREATE", "MODIFY", "MOVE"}:
        return "#7bba55 bold"
    if operation.startswith("DELETE"):
        return "#c85f5f bold"
    return "#b28443 bold"


def _file_label(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

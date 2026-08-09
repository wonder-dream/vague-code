"""Sidebar row rendering: status dot + truncated title (ADR-0026)."""

from __future__ import annotations

from rich.markup import escape

RUNNING_DOT = "[#7bba55]●[/]"
IDLE_DOT = "[#808185]·[/]"
CURRENT_MARK = "[#7bba55]>[/]"
MAX_TITLE = 18
DELETE_MARK = "  [#66666b]✕[/]"


def session_row_text(title: str, busy: bool, is_current: bool) -> str:
    dot = RUNNING_DOT if busy else IDLE_DOT
    cursor = CURRENT_MARK if is_current else " "
    text = (title or "会话").strip()
    if len(text) > MAX_TITLE:
        text = text[: MAX_TITLE - 1] + "…"
    return f"{cursor} {dot} {escape(text)}"

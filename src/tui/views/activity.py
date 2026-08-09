"""Activity-line and turn-metrics rendering helpers for the XClaw TUI.

Ported from the firstcoder TUI reference (app/activity_view.py), keeping the
pure rendering functions used by the activity line and tool compaction.
"""

from __future__ import annotations

from rich.markup import escape


def activity_markup(text: str) -> str:
    color = "#7bba55"
    if text.startswith("waiting"):
        color = "#b28443"
    elif text.startswith("running"):
        color = "#808185"
    elif text.startswith("streaming"):
        color = "#6e6d72"
    elif text.startswith("error"):
        color = "#c85f5f"
    return f"[{color}]{escape(text)}[/]"


def truncate_activity_text(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "."


def turn_metrics_text(elapsed_seconds: float, tool_count: int) -> str:
    elapsed = format_elapsed_time(elapsed_seconds)
    return f"{elapsed} · {tool_count} {'tool' if tool_count == 1 else 'tools'}"


def format_elapsed_time(elapsed_seconds: float) -> str:
    if elapsed_seconds < 60:
        return f"{max(0.0, elapsed_seconds):.1f}s"
    total_seconds = int(max(0, elapsed_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s"


def compact_tool_content(text: str, max_chars: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return "." * max_chars
    return normalized[: max_chars - 3] + "..."


def tool_activity_line_text(name: str, status: str) -> str:
    if status == "running":
        return f"running · {name}"
    if status == "success":
        return post_tool_reasoning_text(name)
    if status == "permission_requested":
        return "waiting · permission"
    if status in {"error", "failed"}:
        return f"error · {name}"
    return f"{status} · {name}"


def post_tool_reasoning_text(name: str) -> str:
    return f"reading {name} result"

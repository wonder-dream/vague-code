"""Topbar markup helpers for the vague-code TUI.

Ported from the firstcoder TUI reference (app/topbar_view.py), without the
provider glow easter egg.
"""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text

PERMISSION_MODE_COLORS = {
    "safe": "#7bba55",
    "normal": "#cfd1d6",
    "autoedit": "#f6b73c",
    "auto": "#ff6b5f",
}

BRAND = "[#7bba55]vaguecode[/]"
SEPARATOR = "[#303238] · [/]"


def _markup_width(markup: str) -> int:
    return len(Text.from_markup(markup).plain)


def _truncate_markup(markup: str, width: int) -> str:
    """Return styled markup constrained to one terminal row."""
    text = Text.from_markup(markup)
    text.truncate(max(0, width), overflow="ellipsis", pad=False)
    return text.markup


def _provider_name_markup(provider: str) -> str:
    return f"[#7bba55]{escape(provider)}[/]"


def _provider_model_markup(provider: str, model: str) -> str:
    return f"{_provider_name_markup(provider)}[#6e6d72]/{escape(model)}[/]"


def _mode_markup(mode: str) -> str:
    color = PERMISSION_MODE_COLORS.get(mode, "#cfd1d6")
    return f"[{color}]{escape(mode)}[/]"


def topbar_markup(
    activity: str,
    provider: str,
    model: str,
    mode: str,
    cwd: str,
    width: int,
    running_count: int = 0,
) -> str:
    """Render the full topbar row, truncating rightmost segments on narrow terms."""
    segments = [
        BRAND,
        f"[#6e6d72]{escape(activity)}[/]",
        _provider_model_markup(provider, model),
        _mode_markup(mode),
        f"[#6e6d72]{escape(cwd)}[/]",
    ]
    if running_count > 0:
        segments.append(f"[#7bba55]{running_count} running[/]")
    joined = SEPARATOR.join(segments)
    if _markup_width(joined) <= width:
        return joined
    if width <= 0:
        return ""
    for drop in range(len(segments) - 1, 0, -1):
        candidate = SEPARATOR.join(segments[: drop + 1])
        if _markup_width(candidate) <= width:
            return _truncate_markup(candidate, width)
    return _truncate_markup(segments[0], width)

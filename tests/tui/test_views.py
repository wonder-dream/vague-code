from vague_code.tui.state import TuiEntryKind, TuiTranscriptEntry
from vague_code.tui.views.activity import (
    activity_markup,
    compact_tool_content,
    format_elapsed_time,
    truncate_activity_text,
    turn_metrics_text,
)
from vague_code.tui.views.topbar import topbar_markup
from vague_code.tui.views.transcript import entry_classes, entry_markdown_text, entry_plain_text
from vague_code.tui.views.welcome import welcome_renderable


def _entry(kind: TuiEntryKind, body: str = "x", status: str | None = None) -> TuiTranscriptEntry:
    return TuiTranscriptEntry(id=1, kind=kind, body=body, label=kind.value, status=status)


# ── activity ─────────────────────────────────────────────────────────────────

def test_activity_markup_colors_by_prefix() -> None:
    assert activity_markup("running · bash") == "[#808185]running · bash[/]"
    assert activity_markup("error · boom") == "[#c85f5f]error · boom[/]"
    assert activity_markup("waiting · permission") == "[#b28443]waiting · permission[/]"
    assert activity_markup("idle · ready") == "[#7bba55]idle · ready[/]"


def test_activity_markup_escapes_text() -> None:
    assert "[[" not in activity_markup("running [bold]x")


def test_truncate_activity_text() -> None:
    assert truncate_activity_text("abc", 3) == "abc"
    assert truncate_activity_text("abcd", 3) == "ab."
    assert truncate_activity_text("abcd", 1) == "a"


def test_turn_metrics_text() -> None:
    assert turn_metrics_text(12.3, 1) == "12.3s · 1 tool"
    assert turn_metrics_text(12.3, 2) == "12.3s · 2 tools"


def test_format_elapsed_time() -> None:
    assert format_elapsed_time(0) == "0.0s"
    assert format_elapsed_time(59.9) == "59.9s"
    assert format_elapsed_time(61) == "1m 1s"
    assert format_elapsed_time(3600) == "1h 0m 0s"


def test_compact_tool_content() -> None:
    assert compact_tool_content("a  b   c") == "a b c"
    long = " ".join(str(i) for i in range(200))
    assert len(compact_tool_content(long)) == 180
    assert compact_tool_content(long).endswith("...")


# ── topbar ───────────────────────────────────────────────────────────────────

def test_topbar_markup_fits_width() -> None:
    markup = topbar_markup("running", "deepseek", "v4-flash", "normal", "vague-code", 200)
    assert "vaguecode" in markup
    assert "deepseek" in markup
    assert "normal" in markup


def test_topbar_markup_truncates_on_narrow_terminal() -> None:
    markup = topbar_markup("running · bash", "deepseek", "v4-flash", "normal", "vague-code", 10)
    assert "vaguecode" in markup
    assert len(markup) < 100


def test_topbar_markup_mode_colors() -> None:
    assert "#f6b73c" in topbar_markup("idle", "d", "m", "autoedit", "cwd", 200)
    assert "#ff6b5f" in topbar_markup("idle", "d", "m", "auto", "cwd", 200)


# ── transcript view ──────────────────────────────────────────────────────────

def test_entry_classes_per_kind() -> None:
    assert entry_classes(_entry(TuiEntryKind.USER)) == "message user-message"
    assert entry_classes(_entry(TuiEntryKind.ASSISTANT)) == "message assistant-message"
    assert entry_classes(_entry(TuiEntryKind.ERROR)) == "message error-message"
    assert entry_classes(_entry(TuiEntryKind.TOOL, status="running")) == "message tool-message tool-running"
    assert entry_classes(_entry(TuiEntryKind.TOOL, status="error")) == "message tool-message tool-failed"


def test_entry_plain_text() -> None:
    assert entry_plain_text(_entry(TuiEntryKind.USER, "hi")) == "user\n  hi"
    assert entry_plain_text(_entry(TuiEntryKind.SYSTEM, "s")) == "s"


def test_entry_markdown_text() -> None:
    assert entry_markdown_text(_entry(TuiEntryKind.ASSISTANT, "body")) == "assistant\n\nbody"


# ── welcome ──────────────────────────────────────────────────────────────────

def test_welcome_renderable_compact_and_full() -> None:
    full = welcome_renderable(compact=False, particle_frame=1)
    assert full.renderable.plain
    compact = welcome_renderable(compact=True)
    assert "vaguecode" in compact.renderable.plain

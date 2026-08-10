"""Transcript-driven conversation view for the XClaw TUI.

The view renders transcript entries as widgets and remembers each entry's
widget for in-place updates by later milestones (streaming, tool activity).
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from src.tui.state import TuiEntryKind, TuiTranscript, TuiTranscriptEntry
from src.tui.views.transcript import entry_classes, entry_markdown_text, entry_plain_text
from src.tui.widgets.common import XClawMarkdown, _plain_static


class ConversationView(VerticalScroll):
    """Vertical transcript that owns the widgets for each entry."""

    def __init__(self, transcript: TuiTranscript | None = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.transcript = transcript or TuiTranscript()

    def add_entry(self, entry: TuiTranscriptEntry) -> None:
        entry.widget = self._widget_for(entry)
        self.mount(entry.widget)
        self.scroll_end(animate=False)

    def _widget_for(self, entry: TuiTranscriptEntry) -> Static | XClawMarkdown:
        classes = entry_classes(entry)
        if entry.kind == TuiEntryKind.ASSISTANT:
            return XClawMarkdown(entry_markdown_text(entry), classes=classes)
        return _plain_static(entry_plain_text(entry), classes=classes)

    def render_transcript(self) -> None:
        """Rebuild all entry widgets from the current transcript (session switch)."""
        self.remove_children()
        for entry in self.transcript.entries:
            entry.widget = None
            self.add_entry(entry)

    def mount_stream_widget(self) -> XClawMarkdown:
        """Mount a streaming Markdown widget for an in-progress assistant entry."""
        widget = XClawMarkdown(
            classes="message assistant-message streaming",
            selectable=False,
        )
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def update_entry(self, entry: TuiTranscriptEntry) -> None:
        widget = entry.widget
        if widget is None:
            return
        if entry.kind == TuiEntryKind.ASSISTANT and isinstance(widget, XClawMarkdown):
            widget.update(entry_markdown_text(entry))
            return
        if hasattr(widget, "update"):
            widget.update(entry_plain_text(entry))

    def clear(self) -> None:
        self.transcript = TuiTranscript()
        self.remove_children()

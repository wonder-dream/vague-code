"""Textual widgets used by the XClaw TUI.

Ported from the firstcoder TUI reference (app/tui_widgets.py) with the
FirstCoder* classes renamed to XClaw*.
"""

from __future__ import annotations

import asyncio

from textual import events
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Markdown, Static, TextArea

from rich.console import RenderableType


class XClawMarkdown(Markdown):
    """Markdown output with selection gated by its render lifecycle."""

    ALLOW_SELECT = True

    def __init__(self, *args, selectable: bool = True, **kwargs) -> None:
        self._selectable = selectable
        super().__init__(*args, **kwargs)

    @property
    def allow_select(self) -> bool:
        return self._selectable

    def set_selectable(self, selectable: bool) -> None:
        """Toggle selection once no more Markdown updates will replace blocks."""

        self._selectable = selectable
        self.refresh()


XClawMarkdown.BLOCKS = {
    name: type(
        f"XClaw{block.__name__}",
        (block,),
        {"ALLOW_SELECT": True},
    )
    for name, block in Markdown.BLOCKS.items()
}


class ComposerTextArea(TextArea):
    """Multiline composer where Enter submits and Shift+Enter inserts a newline."""

    class Submitted(Message):
        pass

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted())
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


def _plain_static(content: RenderableType = "", *args, **kwargs) -> Static:
    kwargs.setdefault("markup", False)
    return Static(content, *args, **kwargs)


def _observe_markdown_update(update_result) -> None:
    future = getattr(update_result, "_future", None)
    if future is None or not hasattr(future, "add_done_callback"):
        return

    def observe_cancelled_update(done_future) -> None:
        try:
            exception = done_future.exception()
        except asyncio.CancelledError:
            return
        if isinstance(exception, asyncio.CancelledError):
            return
        if exception is not None:
            raise exception

    future.add_done_callback(observe_cancelled_update)


class XClawScreen(Screen[None]):
    """Notify the app after Textual has committed a new terminal size."""

    @staticmethod
    def _selection_is_blocked_by_streaming_markdown(widget) -> bool:
        """Reject a leaf while any owning Markdown document is still updating."""

        parent = widget
        while parent is not None:
            if isinstance(parent, XClawMarkdown) and not parent.allow_select:
                return True
            parent = getattr(parent, "parent", None)
        return False

    def get_widget_and_offset_at(self, x: int, y: int):
        widget, offset = super().get_widget_and_offset_at(x, y)
        if widget is not None and self._selection_is_blocked_by_streaming_markdown(widget):
            return None, None
        return widget, offset

    def _screen_resized(self, size) -> None:
        super()._screen_resized(size)
        callback = getattr(self.app, "_on_terminal_resized", None)
        if callback is not None:
            callback()

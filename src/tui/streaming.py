"""Streaming Markdown rendering for the XClaw TUI.

Ported from the firstcoder TUI reference (app/tui_view.py streaming section):
a per-turn text buffer flushed to the Markdown widget on a 0.2s timer, with a
guard against overlapping updates so final text is never overwritten.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.tui.state import TuiEntryKind, TuiTranscript, TuiTranscriptEntry
from src.tui.views.transcript import entry_markdown_text
from src.tui.widgets.common import XClawMarkdown, _observe_markdown_update
from src.tui.widgets.conversation import ConversationView

_TURN_START_ACTIVITY = "planning next step..."
_TURN_DONE_ACTIVITY = "done"
_ERROR_ACTIVITY = "error"


class StreamingMixin:
    """Per-turn streaming state and rendering helpers.

    The host app must define: `transcript`, `_chat_turn_token`,
    `STREAM_RENDER_INTERVAL_SECONDS`, and expose `query_one` / `set_timer` /
    `call_later` / `run_worker` (Textual App).
    """

    STREAM_RENDER_INTERVAL_SECONDS = 0.2

    transcript: TuiTranscript
    _chat_turn_token: int
    _chat_busy: bool
    _activity_text: str
    _turn_started_at: float
    _turn_tool_count: int
    _tool_entries: dict[str, TuiTranscriptEntry]
    _tool_names: dict[str, str]
    _tool_args_buffer: dict[str, str]
    _running_tool_call_ids: set[str]
    workers: Any

    if TYPE_CHECKING:
        # Provided by the Textual host app; type-checking stubs only.
        def query_one(self, selector: Any, expect_type: Any = None, **kwargs: Any) -> Any: ...

        def set_timer(self, *args: Any, **kwargs: Any) -> Any: ...

        def call_later(self, *args: Any, **kwargs: Any) -> Any: ...

        def run_worker(self, *args: Any, **kwargs: Any) -> Any: ...

        def _topbar_text(self) -> str: ...

    def _reset_stream_state(self) -> None:
        self._stream_text_buffer = ""
        self._stream_text_widget: XClawMarkdown | None = None
        self._stream_text_entry: TuiTranscriptEntry | None = None
        self._stream_rendered_text = ""
        self._stream_flush_timer = None
        self._stream_markdown_update = None
        self._stream_finalizations: dict[XClawMarkdown, object] = {}
        self._stream_finalized = False
        self._stream_segment_closed_for_tool = False

    def _is_current_chat_turn(self, token: int) -> bool:
        return token == self._chat_turn_token

    def _set_activity(self, text: str) -> None:
        self._activity_text = text
        activity = self.query_one("#activity")
        if hasattr(activity, "update_activity"):
            activity.update_activity(text)
        self._refresh_topbar()

    def _refresh_topbar(self) -> None:
        topbar = self.query_one("#topbar")
        if hasattr(topbar, "update"):
            topbar.update(self._topbar_text())

    def _write_line(
        self,
        text: str,
        *,
        kind: TuiEntryKind = TuiEntryKind.SYSTEM,
        label: str | None = None,
        status: str | None = None,
    ) -> TuiTranscriptEntry:
        entry = self.transcript.add(kind, text, label=label, status=status)
        output = self.query_one("#output", ConversationView)
        output.add_entry(entry)
        return entry

    def _write_markdown_message(self, content: str) -> TuiTranscriptEntry:
        entry = self.transcript.add(TuiEntryKind.ASSISTANT, content)
        output = self.query_one("#output", ConversationView)
        output.add_entry(entry)
        return entry

    def _scroll_output_end_if_pinned(self) -> None:
        output = self.query_one("#output", ConversationView)
        if not hasattr(output, "scroll_end"):
            return
        scroll_y = float(getattr(output, "scroll_y", 0) or 0)
        max_scroll_y = float(getattr(output, "max_scroll_y", 0) or 0)
        if max_scroll_y and scroll_y < max_scroll_y - 1:
            return
        output.scroll_end(animate=False)

    # ── streaming ────────────────────────────────────────────────────────────

    def _append_stream_text(self, text: str) -> None:
        if self._stream_segment_closed_for_tool:
            self._start_new_stream_segment()
        self._stream_text_buffer += text
        if self._stream_text_entry is None:
            self._stream_text_entry = self.transcript.add(
                TuiEntryKind.ASSISTANT, self._stream_text_buffer
            )
        else:
            self._stream_text_entry.body = self._stream_text_buffer
        output = self.query_one("#output", ConversationView)
        if self._stream_text_widget is None:
            self._stream_text_widget = output.mount_stream_widget()
        if not self._stream_rendered_text:
            self._flush_stream_text()
        else:
            self._schedule_stream_flush()

    def _close_stream_segment_for_tool(self) -> None:
        if self._stream_text_widget is None and not self._stream_text_buffer:
            return
        self._finalize_stream_widget()
        self._stream_segment_closed_for_tool = True

    def _start_new_stream_segment(self) -> None:
        self._stream_text_buffer = ""
        self._stream_text_widget = None
        self._stream_text_entry = None
        self._stream_rendered_text = ""
        self._stream_flush_timer = None
        self._stream_markdown_update = None
        self._stream_finalized = False
        self._stream_segment_closed_for_tool = False

    def _schedule_stream_flush(self) -> None:
        if self._stream_flush_timer is not None:
            return
        self._stream_flush_timer = self.set_timer(
            self.STREAM_RENDER_INTERVAL_SECONDS,
            self._flush_stream_text,
            name="stream-markdown-flush",
        )

    def _flush_stream_text(self) -> bool:
        self._stream_flush_timer = None
        widget = self._stream_text_widget
        if widget is None:
            return False
        if self._stream_rendered_text == self._stream_text_buffer:
            return False
        if self._stream_markdown_update is not None:
            return False
        self._stream_rendered_text = self._stream_text_buffer
        if self._stream_text_entry is None:
            return False
        update_result = widget.update(entry_markdown_text(self._stream_text_entry))
        self._track_stream_markdown_update(update_result)
        _observe_markdown_update(update_result)
        self._scroll_output_end_if_pinned()
        return True

    def _track_stream_markdown_update(self, update_result) -> None:
        future = getattr(update_result, "_future", None)
        if future is None or not hasattr(future, "add_done_callback"):
            return
        self._stream_markdown_update = update_result

        def finish_latest_update(_future) -> None:
            self.call_later(self._finish_stream_markdown_update, update_result)

        future.add_done_callback(finish_latest_update)

    def _finish_stream_markdown_update(self, update_result) -> None:
        if self._stream_markdown_update is not update_result:
            return
        self._stream_markdown_update = None
        if not self._stream_finalized and self._stream_rendered_text != self._stream_text_buffer:
            self._schedule_stream_flush()

    def _finalize_stream_widget(self) -> None:
        widget = self._stream_text_widget
        if widget is None or self._stream_finalized:
            return
        timer = self._stream_flush_timer
        if timer is not None:
            timer.stop()
        self._stream_flush_timer = None
        entry = self._stream_text_entry
        if entry is None:
            self._stream_finalized = True
            return
        final_markdown = entry_markdown_text(entry)
        pending_update = self._stream_markdown_update
        self._stream_markdown_update = None
        self._stream_rendered_text = self._stream_text_buffer
        self._stream_finalized = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            update_result = widget.update(final_markdown)
            _observe_markdown_update(update_result)
            widget.set_selectable(True)
            return
        completion = loop.create_future()
        self._stream_finalizations[widget] = completion

        async def finalize() -> None:
            try:
                if pending_update is not None:
                    await pending_update
                await widget.update(final_markdown)
                widget.set_selectable(True)
            except BaseException as error:
                if not completion.done():
                    completion.set_exception(error)
            else:
                if not completion.done():
                    completion.set_result(None)

        self.run_worker(
            finalize(),
            exclusive=False,
            group="stream-finalization",
            exit_on_error=False,
        )

    # ── turn flow ────────────────────────────────────────────────────────────

    def _begin_chat_turn(self, text: str) -> int:
        self._chat_turn_token += 1
        token = self._chat_turn_token
        self._chat_busy = True
        self._reset_stream_state()
        self._turn_started_at = 0.0
        self._turn_tool_count = 0
        self._tool_entries = {}
        self._tool_names = {}
        self._tool_args_buffer = {}
        self._running_tool_call_ids = set()
        self._set_activity(_TURN_START_ACTIVITY)
        return token

    def _interrupt_chat_turn(self) -> None:
        self._chat_turn_token += 1
        self._chat_busy = False
        self._reset_stream_state()
        for worker in list(self.workers):
            if worker.state.name == "RUNNING" and worker.group != "stream-finalization":
                worker.cancel()
        self._set_activity("interrupted")
        self._write_line("已中断当前回合。", kind=TuiEntryKind.SYSTEM)

from __future__ import annotations

from src.agent.ir import (
    ArgsDelta,
    MessageEnd,
    MessageStart,
    NullVisitor,
    RetryNotice,
    TextDelta,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolUseEnd,
    ToolUseStart,
)

if __name__ == "__main__":
    from src.tui.widgets.conversation import ConversationView
else:
    ConversationView = None  # type: ignore[misc]


class TextualStreamVisitor(NullVisitor):
    """Translates StreamEvent objects into ConversationView updates."""

    def __init__(self, conv: ConversationView) -> None:
        self._conv = conv
        self._current_tool_name: str | None = None
        self._thinking_open: bool = False

    def message_start(self, ev: MessageStart) -> None:
        pass

    def thinking_start(self, ev: ThinkingStart) -> None:
        self._thinking_open = True
        self._conv.start_thinking()

    def thinking_delta(self, ev: ThinkingDelta) -> None:
        self._conv.add_thinking_delta(ev.delta)

    def thinking_end(self, ev: ThinkingEnd) -> None:
        self._thinking_open = False
        self._conv.end_thinking()

    def text_delta(self, ev: TextDelta) -> None:
        self._conv.append_text(ev.delta)

    def tool_use_start(self, ev: ToolUseStart) -> None:
        self._current_tool_name = ev.name
        self._conv.start_tool(ev.name)

    def args_delta(self, ev: ArgsDelta) -> None:
        if self._current_tool_name:
            self._conv.append_tool_args(ev.delta)

    def tool_use_end(self, ev: ToolUseEnd) -> None:
        self._current_tool_name = None

    def message_end(self, ev: MessageEnd) -> None:
        self._conv.finalize_message(ev.stop_reason.value if ev.stop_reason else "?")

    def retry_notice(self, ev: RetryNotice) -> None:
        self._conv.add_retry_notice(ev)

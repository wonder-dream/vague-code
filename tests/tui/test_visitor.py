from __future__ import annotations

from src.agent.ir import (
    ArgsDelta,
    MessageEnd,
    MessageStart,
    RetryNotice,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolUseEnd,
    ToolUseStart,
)
from src.tui.visitor import TextualStreamVisitor


class _FakeConv:
    """Mock ConversationView that records method calls."""

    def __init__(self):
        self.calls: list[str] = []
        self._thinking_content: list[str] = []
        self._blocks: list = []

    def start_thinking(self) -> None:
        self.calls.append("start_thinking")

    def add_thinking_delta(self, delta: str) -> None:
        self.calls.append(f"thinking_delta:{delta}")

    def end_thinking(self) -> None:
        self.calls.append("end_thinking")

    def append_text(self, delta: str) -> None:
        self.calls.append(f"text:{delta}")

    def start_tool(self, name: str) -> None:
        self.calls.append(f"start_tool:{name}")

    def append_tool_args(self, delta: str) -> None:
        self.calls.append(f"args:{delta}")

    def finalize_message(self, stop_reason: str) -> None:
        self.calls.append(f"finalize:{stop_reason}")

    def add_retry_notice(self, ev: RetryNotice) -> None:
        self.calls.append(f"retry:{ev.reason}")

    def toggle_thinking(self) -> None:
        pass

    def select_next(self) -> None:
        pass

    def select_prev(self) -> None:
        pass

    def toggle_current_expand(self) -> None:
        pass


class TestTextualStreamVisitor:
    def test_thinking_sequence(self) -> None:
        conv = _FakeConv()
        v = TextualStreamVisitor(conv)

        v.thinking_start(ThinkingStart())
        v.thinking_delta(ThinkingDelta("step1"))
        v.thinking_delta(ThinkingDelta("step2"))
        v.thinking_end(ThinkingEnd(signature=None))

        assert "start_thinking" in conv.calls
        assert "thinking_delta:step1" in conv.calls
        assert "thinking_delta:step2" in conv.calls
        assert "end_thinking" in conv.calls

    def test_text_delta(self) -> None:
        conv = _FakeConv()
        v = TextualStreamVisitor(conv)

        v.text_delta(TextDelta("hello"))
        v.text_delta(TextDelta(" world"))

        assert conv.calls == ["text:hello", "text: world"]

    def test_tool_use_sequence(self) -> None:
        conv = _FakeConv()
        v = TextualStreamVisitor(conv)

        v.tool_use_start(ToolUseStart(id="call1", name="read_file"))
        v.args_delta(ArgsDelta(id="call1", delta='{"path": "src/main.py"'))
        v.args_delta(ArgsDelta(id="call1", delta='"}'))
        v.tool_use_end(ToolUseEnd(id="call1"))

        assert "start_tool:read_file" in conv.calls
        assert "args:{\"path\": \"src/main.py\"" in conv.calls

    def test_message_end(self) -> None:
        conv = _FakeConv()
        v = TextualStreamVisitor(conv)

        v.message_end(MessageEnd(stop_reason=StopReason.end_turn))

        assert "finalize:end_turn" in conv.calls

    def test_retry_notice(self) -> None:
        conv = _FakeConv()
        v = TextualStreamVisitor(conv)

        v.retry_notice(RetryNotice(attempt=1, delay_s=2.0, reason="rate_limit"))

        assert "retry:rate_limit" in conv.calls

    def test_message_start_noop(self) -> None:
        conv = _FakeConv()
        v = TextualStreamVisitor(conv)

        v.message_start(MessageStart(model="deepseek-v4"))
        # message_start should do nothing visible
        assert len(conv.calls) == 0

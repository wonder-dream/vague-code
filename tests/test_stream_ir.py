from __future__ import annotations

from vague_code.agent.ir import (
    ArgsDelta,
    MessageEnd,
    MessageStart,
    NormalizedUsage,
    NullVisitor,
    RetryNotice,
    StopReason,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolUseEnd,
    ToolUseStart,
    dispatch_event,
)


class TestStreamEventToDict:
    def test_message_start(self):
        d = MessageStart(model="deepseek-chat").to_dict()
        assert d == {"stream_type": "message_start", "model": "deepseek-chat"}

    def test_thinking_start(self):
        d = ThinkingStart().to_dict()
        assert d == {"stream_type": "thinking_start"}

    def test_thinking_delta(self):
        d = ThinkingDelta(delta="hello").to_dict()
        assert d == {"stream_type": "thinking_delta", "delta": "hello"}

    def test_thinking_end_none(self):
        d = ThinkingEnd(signature=None).to_dict()
        assert d == {"stream_type": "thinking_end"}

    def test_thinking_end_with_sig(self):
        d = ThinkingEnd(signature="sig123").to_dict()
        assert d == {"stream_type": "thinking_end", "signature": "sig123"}

    def test_text_delta(self):
        d = TextDelta(delta="hi").to_dict()
        assert d == {"stream_type": "text_delta", "delta": "hi"}

    def test_tool_use_start(self):
        d = ToolUseStart(id="call_1", name="read_file").to_dict()
        assert d == {"stream_type": "tool_use_start", "id": "call_1", "name": "read_file"}

    def test_args_delta(self):
        d = ArgsDelta(id="call_1", delta='{"path":').to_dict()
        assert d == {"stream_type": "args_delta", "id": "call_1", "delta": '{"path":'}

    def test_tool_use_end(self):
        d = ToolUseEnd(id="call_1").to_dict()
        assert d == {"stream_type": "tool_use_end", "id": "call_1"}

    def test_message_end(self):
        usage = NormalizedUsage(input_tokens=10, output_tokens=5)
        d = MessageEnd(
            stop_reason=StopReason.end_turn,
            finish_reason="stop",
            truncated=False,
            usage=usage,
        ).to_dict()
        assert d["stream_type"] == "message_end"
        assert d["stop_reason"] == "end_turn"
        assert d["finish_reason"] == "stop"
        assert d["truncated"] is False
        assert d["usage"]["input_tokens"] == 10

    def test_message_end_usage_none(self):
        d = MessageEnd(stop_reason=StopReason.unknown).to_dict()
        assert d["usage"]["input_tokens"] == 0

    def test_retry_notice(self):
        d = RetryNotice(attempt=2, delay_s=4.0, reason="rate_limit").to_dict()
        assert d == {"stream_type": "retry_notice", "attempt": 2, "delay_s": 4.0, "reason": "rate_limit"}


class TestThinkingBlockSignature:
    def test_signature_default_none(self):
        b = ThinkingBlock(text="think")
        assert b.signature is None

    def test_signature_explicit(self):
        b = ThinkingBlock(text="think", signature="sig")
        assert b.signature == "sig"

    def test_to_dict_without_sig(self):
        d = ThinkingBlock(text="think").to_dict()
        assert d == {"type": "thinking", "text": "think"}

    def test_to_dict_with_sig(self):
        d = ThinkingBlock(text="think", signature="sig").to_dict()
        assert d == {"type": "thinking", "text": "think", "signature": "sig"}


class RecordingVisitor:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def message_start(self, ev):    self.calls.append(("message_start", ev))
    def thinking_start(self, ev):   self.calls.append(("thinking_start", ev))
    def thinking_delta(self, ev):   self.calls.append(("thinking_delta", ev))
    def thinking_end(self, ev):     self.calls.append(("thinking_end", ev))
    def text_delta(self, ev):       self.calls.append(("text_delta", ev))
    def tool_use_start(self, ev):   self.calls.append(("tool_use_start", ev))
    def args_delta(self, ev):       self.calls.append(("args_delta", ev))
    def tool_use_end(self, ev):     self.calls.append(("tool_use_end", ev))
    def message_end(self, ev):      self.calls.append(("message_end", ev))
    def retry_notice(self, ev):     self.calls.append(("retry_notice", ev))


class TestDispatchEvent:
    def test_all_types_dispatched(self):
        v = RecordingVisitor()
        dispatch_event(MessageStart(model="m"), v)
        dispatch_event(ThinkingStart(), v)
        dispatch_event(ThinkingDelta(delta="d"), v)
        dispatch_event(ThinkingEnd(signature=None), v)
        dispatch_event(TextDelta(delta="t"), v)
        dispatch_event(ToolUseStart(id="1", name="read"), v)
        dispatch_event(ArgsDelta(id="1", delta="{}"), v)
        dispatch_event(ToolUseEnd(id="1"), v)
        dispatch_event(MessageEnd(stop_reason=StopReason.end_turn), v)
        dispatch_event(RetryNotice(attempt=1, delay_s=2.0, reason="timeout"), v)
        assert len(v.calls) == 10
        names = [c[0] for c in v.calls]
        assert names == [
            "message_start", "thinking_start", "thinking_delta", "thinking_end",
            "text_delta", "tool_use_start", "args_delta", "tool_use_end", "message_end",
            "retry_notice",
        ]


class TestNullVisitor:
    def test_no_op_on_any_event(self):
        v = NullVisitor()
        dispatch_event(MessageStart(model="m"), v)
        dispatch_event(ThinkingStart(), v)
        # should not raise
        assert True

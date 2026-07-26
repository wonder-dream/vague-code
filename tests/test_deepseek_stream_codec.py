from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.codecs.deepseek import DeepSeekStreamDecoder
from src.agent.ir import (
    StopReason,
    dispatch_event,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _load_fixture(name: str) -> dict:
    path = GOLDEN_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Golden transcript tests ──────────────────────────────────────────────────

GOLDEN_FIXTURES = [
    "stream_text_only.json",
    "stream_reasoning_then_text.json",
    "stream_reasoning_empty_close.json",
    "stream_single_tool.json",
    "stream_multi_tool_interleaved.json",
    "stream_truncated.json",
    "stream_no_usage.json",
]


class TestGoldenStreamDecode:
    @pytest.mark.parametrize("fixture", GOLDEN_FIXTURES)
    def test_golden(self, fixture: str):
        data = _load_fixture(fixture)
        decoder = DeepSeekStreamDecoder()
        events: list[dict] = []
        for chunk in data["chunks"]:
            evs = decoder.decode_chunk(chunk)
            events.extend(e.to_dict() for e in evs)
        events.extend(e.to_dict() for e in decoder.flush())

        expected = data["expected"]
        assert len(events) == len(expected), (
            f"Event count mismatch:\n"
            f"  got:      {[e['stream_type'] for e in events]}\n"
            f"  expected: {[e['stream_type'] for e in expected]}"
        )
        for i, (got, exp) in enumerate(zip(events, expected)):
            assert got == exp, f"Event {i} mismatch:\n  got:      {got}\n  expected: {exp}"


# ── Unit tests ───────────────────────────────────────────────────────────────


class TestDeepSeekStreamDecoder:
    def test_empty_chunks(self):
        """零 chunk 仅 flush → MessageEnd(unknown)"""
        decoder = DeepSeekStreamDecoder()
        evs = list(decoder.flush())
        assert len(evs) == 1
        assert evs[0].to_dict()["stream_type"] == "message_end"
        assert evs[0].stop_reason == StopReason.unknown

    def test_choice_not_dict(self):
        """choices[0] 非 dict：忽略（错误防御 R3 变体）"""
        decoder = DeepSeekStreamDecoder()
        evs = decoder.decode_chunk({"choices": [None]})
        assert evs == []

    def test_choice_missing_delta(self):
        """choice 无 delta 键：应正常工作"""
        decoder = DeepSeekStreamDecoder()
        # finish 但不带 delta
        evs = decoder.decode_chunk(
            {"choices": [{"finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )
        assert len(evs) == 1
        assert evs[0].to_dict()["stream_type"] == "message_end"

    def test_error_chunk_raises(self):
        decoder = DeepSeekStreamDecoder()
        from src.agent.ir import StreamDisconnect
        with pytest.raises(StreamDisconnect, match="stream error"):
            decoder.decode_chunk({"error": {"message": "overloaded"}})

    def test_usage_only_chunk(self):
        """评审修复 R3a：usage 先到、finish 后到乱序"""
        decoder = DeepSeekStreamDecoder()
        # chunk1: text
        evs = decoder.decode_chunk({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]})
        assert len(evs) == 1 and evs[0].to_dict()["stream_type"] == "text_delta"

        # chunk2: usage-only（无 choices 键，调用 encode_request 时会丢 model/id 等）
        evs = decoder.decode_chunk({"usage": {"prompt_tokens": 2, "completion_tokens": 1}})
        assert evs == []  # finish 未到，不发射

        # chunk3: finish 带空 delta
        evs = decoder.decode_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        assert len(evs) == 1
        me = evs[0].to_dict()
        assert me["stream_type"] == "message_end"
        assert me["usage"]["input_tokens"] == 2  # 来自 chunk2
        assert me["finish_reason"] == "stop"

    def test_finish_chunk_with_content(self):
        """评审修复 R4（语义反转）：finish=tool_calls 同 chunk 带 content，照常产 TextDelta"""
        decoder = DeepSeekStreamDecoder()
        evs = decoder.decode_chunk({
            "choices": [{
                "delta": {"content": "说明"},
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5},
        })
        types = [e.to_dict()["stream_type"] for e in evs]
        assert "text_delta" in types, f"content should produce TextDelta, got {types}"
        assert "message_end" in types

    def test_flush_twice_idempotent(self):
        """flush 幂等：第二次 flush 返回空"""
        decoder = DeepSeekStreamDecoder()
        decoder.decode_chunk({"choices": [{"delta": {"content": "x"}, "finish_reason": None}]})
        r1 = decoder.flush()
        assert len(r1) == 1
        r2 = decoder.flush()
        assert r2 == []

    def test_reasoning_empty_string_does_not_produce_delta(self):
        """评审修复 R2 补充：reasoning_content="" 无 ThinkingDelta(delta="")"""
        decoder = DeepSeekStreamDecoder()
        evs = decoder.decode_chunk({
            "choices": [{"delta": {"reasoning_content": "", "content": "hi"}, "finish_reason": None}]
        })
        for ev in evs:
            d = ev.to_dict()
            if d["stream_type"] == "thinking_delta":
                assert d["delta"] != "", "delta should not be empty string"

    def test_tool_interleaved_three_indices(self):
        """三 tool 交错，finish 时按首见顺序收尾"""
        decoder = DeepSeekStreamDecoder()
        chunks = [
            {"choices": [{"delta": {"tool_calls": [{"index": 2, "id": "c", "type": "function", "function": {"name": "g"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "type": "function", "function": {"name": "x"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "b", "type": "function", "function": {"name": "y"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ]
        decoder = DeepSeekStreamDecoder()
        events: list[dict] = []
        for c in chunks:
            events.extend(e.to_dict() for e in decoder.decode_chunk(c))
        events.extend(e.to_dict() for e in decoder.flush())

        tool_use_starts = [e for e in events if e["stream_type"] == "tool_use_start"]
        tool_use_ends = [e for e in events if e["stream_type"] == "tool_use_end"]
        assert [t["id"] for t in tool_use_starts] == ["c", "a", "b"]  # 首见顺序
        assert [t["id"] for t in tool_use_ends] == ["c", "a", "b"]  # 与 start 一致

    def test_dispatch_event_does_not_crash(self):
        """所有 StreamEvent 类型都能正常 dispatch"""
        from src.agent.ir import NullVisitor

        decoder = DeepSeekStreamDecoder()
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "think", "content": "", "finish_reason": None}}]},
            {"choices": [{"delta": {"content": "text"}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1", "type": "function", "function": {"name": "read"}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ]
        v = NullVisitor()
        for c in chunks:
            for ev in decoder.decode_chunk(c):
                dispatch_event(ev, v)
        for ev in decoder.flush():
            dispatch_event(ev, v)
        # should not raise

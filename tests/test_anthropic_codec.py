from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.codecs.anthropic import (
    AnthropicStreamDecoder,
    decode_response,
    encode_request,
)
from src.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    StreamDisconnect,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

GOLDEN_DIR = Path(__file__).parent / "golden" / "anthropic"


# ── encode tests ─────────────────────────────────────────────────


def test_encode_empty_messages_raises():
    with pytest.raises(ValueError, match="cannot be empty"):
        encode_request([])


def test_encode_text_only():
    messages = [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi there!"),
    ]
    body = encode_request(messages)
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == [{"type": "text", "text": "Hello"}]
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["content"] == [{"type": "text", "text": "Hi there!"}]


def test_encode_tool_use():
    messages = [
        Message(role="user", content="read file"),
        Message(role="assistant", content=[
            TextBlock(text="Reading..."),
            ToolUseBlock(id="tu_01", name="read_file", input={"path": "x.py"}),
        ]),
    ]
    body = encode_request(messages)
    blocks = body["messages"][1]["content"]
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "tool_use"
    assert blocks[1]["id"] == "tu_01"
    assert blocks[1]["name"] == "read_file"


def test_encode_tool_result():
    messages = [
        Message(role="user", content=""),
        Message(role="assistant", content=[ToolUseBlock(id="tu_01", name="read_file", input={"path": "x.py"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="tu_01", content="file contents")]),
    ]
    body = encode_request(messages)
    wire = body["messages"]
    assert len(wire) == 3
    assert wire[2]["role"] == "user"
    blocks = wire[2]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "tu_01"
    assert blocks[0]["content"] == "file contents"
    assert blocks[0].get("is_error") is False


def test_encode_consecutive_user_merged():
    messages = [
        Message(role="user", content="first"),
        Message(role="user", content="second"),
    ]
    body = encode_request(messages)
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    blocks = body["messages"][0]["content"]
    assert len(blocks) == 2
    assert blocks[0]["text"] == "first"
    assert blocks[1]["text"] == "second"


def test_encode_consecutive_assistant_merged():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
        Message(role="assistant", content="world"),
    ]
    body = encode_request(messages)
    assert len(body["messages"]) == 2
    assert len(body["messages"][1]["content"]) == 2


def test_encode_unsigned_thinking_skipped():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content=[
            ThinkingBlock(text="unsigned thought", signature=None),
            TextBlock(text="response"),
        ]),
    ]
    body = encode_request(messages)
    blocks = body["messages"][1]["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"


def test_encode_signed_thinking_included():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content=[
            ThinkingBlock(text="signed thought", signature="sig_01"),
            TextBlock(text="response"),
        ]),
    ]
    body = encode_request(messages)
    blocks = body["messages"][1]["content"]
    assert len(blocks) == 2
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["signature"] == "sig_01"
    assert blocks[0]["thinking"] == "signed thought"


def test_encode_empty_content_fallback():
    messages = [
        Message(role="user", content=[ToolResultBlock(tool_use_id="tu_01", content="")]),
        Message(role="assistant", content="ok"),
    ]
    body = encode_request(messages)
    blocks = body["messages"][0]["content"]
    assert blocks[0]["content"] == "(empty)"


def test_encode_tools():
    tools = [
        ToolSpec(name="read_file", description="Read a file", parameters={"type": "object", "properties": {"path": {"type": "string"}}}),
    ]
    body = encode_request(messages=[Message(role="user", content="hi")], tools=tools)
    assert "tools" in body
    assert body["tools"][0]["name"] == "read_file"
    assert "input_schema" in body["tools"][0]
    assert "type" not in body["tools"][0]


def test_encode_config_filtered():
    body = encode_request(
        messages=[Message(role="user", content="hi")],
        config={"temperature": 0.7, "max_tokens": 1000, "unknown_key": "drop", "thinking": {"type": "enabled", "budget_tokens": 2000}},
    )
    assert body.get("temperature") == 0.7
    assert body.get("max_tokens") == 1000
    assert "unknown_key" not in body
    assert body.get("thinking") == {"type": "enabled", "budget_tokens": 2000}


def test_encode_empty_assistant_raises():
    with pytest.raises(ValueError, match="no valid content"):
        messages = [
            Message(role="user", content="hi"),
            Message(role="assistant", content=[ThinkingBlock(text="thought", signature=None)]),
        ]
        encode_request(messages)


def test_encode_system_message():
    body = encode_request([
        Message(role="system", content="you are an agent"),
        Message(role="user", content="hi"),
    ])
    assert "system" in body
    assert "agent" in body["system"]
    assert all(m["role"] != "system" for m in body["messages"])


def test_encode_system_then_user():
    body = encode_request([
        Message(role="system", content="be helpful"),
        Message(role="user", content="hi"),
    ])
    assert "system" in body
    assert body["messages"][0]["role"] == "user"
# ── decode (golden transcript) ──────────────────────────────────


def _load_golden(name: str) -> tuple[dict, dict]:
    raw = json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    expected = json.loads((GOLDEN_DIR / f"{name}.message.json").read_text(encoding="utf-8"))
    return raw, expected


@pytest.mark.parametrize("scenario", ["text_only", "single_tool_call", "multi_tool_call", "with_thinking"])
def test_decode_golden(scenario: str):
    raw, expected = _load_golden(scenario)
    result: ModelResponse = decode_response(raw)
    assert result.to_dict() == expected


def test_decode_empty_content():
    result = decode_response({"id": "m1", "type": "message", "role": "assistant", "content": [], "model": "c", "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert len(result.message.content) == 1
    assert isinstance(result.message.content[0], TextBlock)
    assert result.message.content[0].text == ""


def test_decode_redacted_thinking_skipped():
    result = decode_response({
        "id": "m1", "type": "message", "role": "assistant",
        "content": [
            {"type": "redacted_thinking", "data": "REDACTED"},
            {"type": "text", "text": "visible"},
        ],
        "model": "c", "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })
    assert len(result.message.content) == 1
    assert result.message.content[0].text == "visible"


def test_decode_stop_reason_end_turn():
    r = decode_response({"id": "m", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "x"}], "model": "c", "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert r.stop_reason == StopReason.end_turn


def test_decode_stop_reason_max_tokens():
    r = decode_response({"id": "m", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "x"}], "model": "c", "stop_reason": "max_tokens", "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert r.stop_reason == StopReason.max_tokens


def test_decode_stop_reason_refusal():
    r = decode_response({"id": "m", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "x"}], "model": "c", "stop_reason": "refusal", "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert r.stop_reason == StopReason.content_filter


def test_decode_stop_reason_null():
    r = decode_response({"id": "m", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "x"}], "model": "c", "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    assert r.stop_reason == StopReason.unknown


def test_decode_unknown_block_skipped():
    result = decode_response({
        "id": "m1", "type": "message", "role": "assistant",
        "content": [
            {"type": "server_tool_use", "id": "st1", "name": "web_search", "input": {}},
            {"type": "text", "text": "done"},
        ],
        "model": "c", "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })
    assert len(result.message.content) == 1
    assert result.message.content[0].text == "done"


def test_decode_usage_mapping():
    r = decode_response({
        "id": "m", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": "x"}],
        "model": "c", "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 10},
    })
    assert r.usage == NormalizedUsage(input_tokens=100, output_tokens=50, cache_read_tokens=20, cache_write_tokens=10)


# ── stream decoder ───────────────────────────────────────────────


def _load_stream_fixture(name: str) -> list[dict]:
    path = GOLDEN_DIR / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]


def test_stream_text_only():
    events = _load_stream_fixture("stream_text_only")
    decoder = AnthropicStreamDecoder()
    output = []
    for ev in events:
        output.extend(decoder.decode_event(ev))

    types = [type(e).__name__ for e in output]
    assert types == ["MessageStart", "TextDelta", "TextDelta", "MessageEnd"]
    assert output[0].model == "claude-opus-4-8"
    assert output[1].delta == "Hello"
    assert output[2].delta == ", world!"
    assert output[3].stop_reason == StopReason.end_turn
    assert output[3].usage.input_tokens == 20
    assert output[3].usage.output_tokens == 5


def test_stream_single_tool():
    events = _load_stream_fixture("stream_single_tool")
    decoder = AnthropicStreamDecoder()
    output = []
    for ev in events:
        output.extend(decoder.decode_event(ev))

    types = [type(e).__name__ for e in output]
    assert "ToolUseStart" in types
    assert "ArgsDelta" in types
    assert "ToolUseEnd" in types
    assert "MessageEnd" in types

    from src.agent.ir import ToolUseStart as TUS
    tus = [e for e in output if isinstance(e, TUS)][0]
    assert tus.id == "toolu_s01"
    assert tus.name == "read_file"

    from src.agent.ir import ArgsDelta as AD
    args = [e for e in output if isinstance(e, AD)]
    assert len(args) == 2

    from src.agent.ir import MessageEnd as ME
    me = [e for e in output if isinstance(e, ME)][0]
    assert me.stop_reason == StopReason.tool_use


def test_stream_multi_tool():
    events = _load_stream_fixture("stream_multi_tool")
    decoder = AnthropicStreamDecoder()
    output = []
    for ev in events:
        output.extend(decoder.decode_event(ev))

    from src.agent.ir import ToolUseStart as TUS
    from src.agent.ir import ToolUseEnd as TUE
    starts = [e for e in output if isinstance(e, TUS)]
    ends = [e for e in output if isinstance(e, TUE)]
    assert len(starts) == 2
    assert len(ends) == 2
    assert starts[0].name == "grep"
    assert starts[1].name == "read_file"
    assert starts[0].id == "toolu_m01"
    assert starts[1].id == "toolu_m02"


def test_stream_thinking():
    events = _load_stream_fixture("stream_thinking")
    decoder = AnthropicStreamDecoder()
    output = []
    for ev in events:
        output.extend(decoder.decode_event(ev))

    from src.agent.ir import ThinkingStart as TS
    from src.agent.ir import ThinkingDelta as TD
    from src.agent.ir import ThinkingEnd as TE
    assert any(isinstance(e, TS) for e in output)
    assert any(isinstance(e, TD) for e in output)
    thinking_ends = [e for e in output if isinstance(e, TE)]
    assert len(thinking_ends) == 1
    assert thinking_ends[0].signature == "Epwxyz"


def test_stream_empty_input_does_not_crash():
    decoder = AnthropicStreamDecoder()
    result = decoder.decode_event({})
    assert result == []


def test_stream_flush_ends_once():
    decoder = AnthropicStreamDecoder()
    first = decoder.flush()
    assert len(first) >= 1
    second = decoder.flush()
    assert len(second) == 0  # flush already emit


def test_stream_no_usage():
    events = _load_stream_fixture("stream_text_only")
    decoder = AnthropicStreamDecoder()
    output = []
    for ev in events:
        output.extend(decoder.decode_event(ev))
    from src.agent.ir import MessageEnd as ME
    me = [e for e in output if isinstance(e, ME)][0]
    assert me.usage is not None


def test_stream_error_event_raises():
    decoder = AnthropicStreamDecoder()
    with pytest.raises(StreamDisconnect, match="API streaming error"):
        decoder.decode_event({
            "type": "error",
            "error": {"type": "overloaded", "message": "Server overloaded"},
        })

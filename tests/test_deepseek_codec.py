from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.codecs.deepseek import encode_request, decode_response
from src.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

GOLDEN_DIR = Path(__file__).parent / "golden"


# ── encode ──────────────────────────────────────────────────────────────


def test_encode_text_only():
    messages = [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi there!"),
    ]
    body = encode_request(messages)
    assert body == {
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
    }


def test_encode_assistant_with_tool_use():
    messages = [
        Message(role="assistant", content=[
            TextBlock(text="Let me check"),
            ToolUseBlock(id="call_1", name="read_file", input={"path": "foo.txt"}),
        ]),
    ]
    body = encode_request(messages)
    assert body == {
        "messages": [
            {
                "role": "assistant",
                "content": "Let me check",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "foo.txt"}'}},
                ],
            }
        ]
    }


def test_encode_assistant_thinking_dropped():
    messages = [
        Message(role="assistant", content=[
            ThinkingBlock(text="I should read the file"),
            ToolUseBlock(id="call_1", name="read_file", input={"path": "foo.txt"}),
        ]),
    ]
    body = encode_request(messages)
    assert body["messages"][0].get("content") is None
    assert len(body["messages"][0]["tool_calls"]) == 1


def test_encode_user_tool_results():
    messages = [
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="call_1", content="file content"),
            ToolResultBlock(tool_use_id="call_2", content="more content"),
        ]),
    ]
    body = encode_request(messages)
    assert body == {
        "messages": [
            {"role": "tool", "tool_call_id": "call_1", "content": "file content"},
            {"role": "tool", "tool_call_id": "call_2", "content": "more content"},
        ]
    }


def test_encode_user_mixed_text_and_tool_result():
    messages = [
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="call_1", content="content_a"),
            TextBlock(text="Thanks, now do more."),
            ToolResultBlock(tool_use_id="call_2", content="content_b"),
        ]),
    ]
    body = encode_request(messages)
    assert body == {
        "messages": [
            {"role": "tool", "tool_call_id": "call_1", "content": "content_a"},
            {"role": "user", "content": "Thanks, now do more."},
            {"role": "tool", "tool_call_id": "call_2", "content": "content_b"},
        ]
    }


def test_encode_user_multiple_text_blocks_merged():
    messages = [
        Message(role="user", content=[
            TextBlock(text="Hello "),
            TextBlock(text="world"),
        ]),
    ]
    body = encode_request(messages)
    assert body == {
        "messages": [
            {"role": "user", "content": "Hello world"},
        ]
    }


def test_encode_orphaned_tool_result_raises():
    msg = Message(role="user", content=[ToolResultBlock(tool_use_id="", content="x")])
    with pytest.raises(ValueError, match="tool_use_id"):
        encode_request([msg])


def test_encode_tools():
    messages = [Message(role="user", content="Read README.md")]
    tools = [ToolSpec(name="read_file", description="Read a file", parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})]
    body = encode_request(messages, tools=tools)
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_encode_config_passthrough():
    messages = [Message(role="user", content="Hi")]
    body = encode_request(messages, config={"model": "deepseek-chat", "temperature": 0.5})
    assert body["temperature"] == 0.5
    assert body["model"] == "deepseek-chat"


# ── decode (golden transcript) ─────────────────────────────────────────


def _load_golden(name: str) -> tuple[dict, dict]:
    raw = json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    expected = json.loads((GOLDEN_DIR / f"{name}.message.json").read_text(encoding="utf-8"))
    return raw, expected


@pytest.mark.parametrize("scenario", ["text_only", "single_tool_call", "multi_tool_call"])
def test_decode_golden(scenario: str):
    raw, expected = _load_golden(scenario)
    result: ModelResponse = decode_response(raw)
    assert result.to_dict() == expected


# ── decode misc ─────────────────────────────────────────────────────────


def test_decode_stop_reason_mapping():
    raw = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    result = decode_response(raw)
    assert result.stop_reason == StopReason.max_tokens


def test_decode_unknown_finish_reason_falls_back():
    raw = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "x"}, "finish_reason": "unknown_xyz"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    result = decode_response(raw)
    assert result.stop_reason == StopReason.stop_sequence


def test_decode_no_content():
    raw = {
        "choices": [{"index": 0, "message": {"role": "assistant"}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    raw["choices"][0]["message"]["tool_calls"] = [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}
    ]
    result = decode_response(raw)
    assert len(result.message.content) == 1
    assert isinstance(result.message.content[0], ToolUseBlock)


def test_decode_usage_no_details():
    raw = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    result = decode_response(raw)
    assert result.usage == NormalizedUsage(input_tokens=5, output_tokens=3, cache_read_tokens=0, cache_write_tokens=0)

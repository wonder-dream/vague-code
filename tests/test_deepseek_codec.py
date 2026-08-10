from __future__ import annotations

import json
from pathlib import Path

import pytest

from vague_code.agent.codecs.deepseek import encode_request, decode_response
from vague_code.agent.ir import (
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


def test_encode_empty_messages_raises():
    with pytest.raises(ValueError, match="messages"):
        encode_request([], None, None)


def test_encode_assistant_thinking_only_does_not_crash():
    msg = Message(role="assistant", content=[ThinkingBlock(text="hmm...")])
    body = encode_request([msg])
    asst_msgs = [m for m in body["messages"] if m["role"] == "assistant"]
    assert len(asst_msgs) == 1
    assert asst_msgs[0].get("content") is None
    assert asst_msgs[0].get("reasoning_content") == "hmm..."


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


def test_encode_assistant_thinking_returned_as_reasoning_content():
    """DeepSeek 思考模式 + 工具调用：reasoning_content 必须回传并参与计费。"""
    messages = [
        Message(role="assistant", content=[
            ThinkingBlock(text="I should read the file"),
            ToolUseBlock(id="call_1", name="read_file", input={"path": "foo.txt"}),
        ]),
    ]
    body = encode_request(messages)
    asst = body["messages"][0]
    assert asst.get("reasoning_content") == "I should read the file"
    assert len(asst["tool_calls"]) == 1


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
    with pytest.raises(ValueError, match="tool_use_id must not be empty"):
        ToolResultBlock(tool_use_id="", content="x")


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


# ── IR validation (B1) ──────────────────────────────────────────────


def test_message_content_none_raises():
    with pytest.raises(ValueError, match="content must not be None"):
        Message(role="user", content=None)


def test_tool_use_block_input_none_raises():
    with pytest.raises(ValueError, match="ToolUseBlock.input must be dict"):
        ToolUseBlock(id="c1", name="read", input=None)


def test_tool_result_block_empty_tool_use_id_raises():
    with pytest.raises(ValueError, match="tool_use_id must not be empty"):
        ToolResultBlock(tool_use_id="", content="x")


def test_normalized_usage_negative_input_tokens_raises():
    with pytest.raises(ValueError, match="input_tokens"):
        NormalizedUsage(input_tokens=-1)


def test_normalized_usage_negative_output_tokens_raises():
    with pytest.raises(ValueError, match="output_tokens"):
        NormalizedUsage(output_tokens=-5)


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


def test_decode_unknown_finish_reason_maps_to_unknown():
    raw = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "x"}, "finish_reason": "unknown_xyz"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    result = decode_response(raw)
    assert result.stop_reason == StopReason.unknown


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


def test_decode_malformed_arguments_fallback():
    raw = {
        "choices": [{"index": 0, "message": {"role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "'}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    result = decode_response(raw)
    assert result.message.content[0].input == {}


def test_encode_system_message():
    body = encode_request([Message(role="system", content="you are an agent")])
    assert body["messages"][0]["role"] == "system"
    assert "agent" in body["messages"][0]["content"]


def test_encode_system_then_user():
    body = encode_request([
        Message(role="system", content="be helpful"),
        Message(role="user", content="hi"),
    ])
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"


# ── B3: decode_response structural defense ──────────────────────────────


def test_decode_response_none_input_raises():
    with pytest.raises(ValueError, match="expected dict"):
        decode_response(None)


def test_decode_response_missing_choices_raises():
    with pytest.raises(ValueError, match="choices"):
        decode_response({})


def test_decode_response_empty_choices_raises():
    with pytest.raises(ValueError, match="choices"):
        decode_response({"choices": []})


def test_decode_response_choices_elem_not_dict():
    with pytest.raises(ValueError, match="choice"):
        decode_response({"choices": ["not_a_dict"]})


def test_decode_response_message_is_none():
    result = decode_response({
        "choices": [{"message": None, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert result.stop_reason == StopReason.end_turn
    assert len(result.message.content) >= 1


def test_decode_response_usage_is_none():
    result = decode_response({
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": None,
    })
    assert result.usage == NormalizedUsage()


def test_decode_response_tool_call_missing_id_skipped():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"type": "function", "function": {"name": "f", "arguments": "{}"}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert len(result.message.content) >= 1


def test_decode_response_tool_call_missing_function_skipped():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function"}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert len(result.message.content) >= 1


def test_encode_user_empty_after_dropping_raises():
    msg = Message(role="user", content=[ThinkingBlock(text="hmm")])
    with pytest.raises(ValueError, match="empty after dropping"):
        encode_request([msg])


def test_decode_prompt_tokens_details_not_dict_survives():
    result = decode_response({
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "prompt_tokens_details": "not_a_dict"},
    })
    assert result.usage.cache_read_tokens == 0


# ── B9: IR validation ──────────────────────────────────────────────────


def test_tool_use_block_empty_id_raises():
    with pytest.raises(ValueError, match="ToolUseBlock.id"):
        ToolUseBlock(id="", name="read", input={})


def test_tool_use_block_empty_name_raises():
    with pytest.raises(ValueError, match="ToolUseBlock.name"):
        ToolUseBlock(id="c1", name="", input={})


def test_message_empty_content_list_raises():
    with pytest.raises(ValueError, match="content list must not be empty"):
        Message(role="user", content=[])


# ── B10: codec robustness ──────────────────────────────────────────────


def test_encode_config_disallowed_keys_filtered():
    body = encode_request(
        [Message(role="user", content="hi")],
        config={"model": "deepseek-chat", "messages": "hijack", "tools": "evil"},
    )
    assert body["model"] == "deepseek-chat"
    assert len(body["messages"]) == 1


def test_decode_tool_call_arguments_as_dict():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": {"path": "x.txt"}}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert result.message.content[0].input == {"path": "x.txt"}


def test_decode_tool_call_arguments_not_str_or_dict():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": [1, 2, 3]}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert result.message.content[0].input == {}

"""OpenAI Responses API codec 测试（ADR-0034）。"""

from __future__ import annotations

from vague_code.agent.codecs.responses import (
    ResponsesStreamDecoder,
    decode_response,
    encode_request,
)
from vague_code.agent.ir import (
    Message,
    MessageEnd,
    MessageStart,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


def test_encode_system_and_user() -> None:
    msgs = [
        Message(role="system", content="you are an agent"),
        Message(role="user", content="hello"),
    ]
    body = encode_request(msgs)
    assert body["instructions"] == "you are an agent"
    assert body["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}]


def test_encode_assistant_text_tool_and_results() -> None:
    msgs = [
        Message(role="assistant", content=[
            TextBlock(text="let me check"),
            ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ]),
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="c1", content="file contents"),
            TextBlock(text="continue"),
        ]),
    ]
    body = encode_request(msgs)
    assert body["input"] == [
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "let me check"}]},
        {"type": "function_call", "call_id": "c1", "name": "read_file",
         "arguments": '{"path": "a.py"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "file contents"},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]


def test_encode_drops_thinking_and_maps_tools() -> None:
    msgs = [
        Message(role="assistant", content=[
            ThinkingBlock(text="think"),
            TextBlock(text="answer"),
        ]),
    ]
    tools = [ToolSpec(name="bash", description="run", parameters={"type": "object"})]
    body = encode_request(msgs, tools)
    assert body["input"] == [{"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": "answer"}]}]
    assert body["tools"] == [{"type": "function", "name": "bash",
                              "description": "run", "parameters": {"type": "object"}}]


def test_encode_max_tokens_mapped() -> None:
    body = encode_request([Message(role="user", content="hi")], config={"max_tokens": 100})
    assert body["max_output_tokens"] == 100
    assert "max_tokens" not in body


def test_decode_completed_with_text() -> None:
    resp = {
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi there"}],
        }],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    r = decode_response(resp)
    assert r.message.content[0].text == "hi there"
    assert r.stop_reason == StopReason.end_turn
    assert r.usage.input_tokens == 10


def test_decode_with_tool_call() -> None:
    resp = {
        "status": "completed",
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "checking"}]},
            {"type": "function_call", "call_id": "fc1", "name": "bash",
             "arguments": '{"command": "ls"}', "id": "fc1"},
        ],
    }
    r = decode_response(resp)
    assert r.stop_reason == StopReason.tool_use
    tool = r.message.content[1]
    assert isinstance(tool, ToolUseBlock)
    assert tool.id == "fc1"
    assert tool.name == "bash"
    assert tool.input == {"command": "ls"}


def test_decode_incomplete_maps_max_tokens() -> None:
    r = decode_response({"status": "incomplete", "output": [], "usage": {}})
    assert r.stop_reason == StopReason.max_tokens


def test_decode_empty_output_defaults_text() -> None:
    r = decode_response({"status": "completed", "output": [], "usage": {}})
    assert len(r.message.content) == 1
    assert r.message.content[0].text == ""


def test_stream_text_and_completed() -> None:
    dec = ResponsesStreamDecoder()
    evs: list = []
    evs += dec.decode_event({"type": "response.created", "response": {"model": "gpt-5.6"}})
    evs += dec.decode_event({"type": "response.output_text.delta", "delta": "Hel"})
    evs += dec.decode_event({"type": "response.output_text.delta", "delta": "lo"})
    evs += dec.decode_event({
        "type": "response.completed",
        "response": {"status": "completed", "output": [], "usage": {"input_tokens": 3, "output_tokens": 2}},
    })
    assert isinstance(evs[0], MessageStart) and evs[0].model == "gpt-5.6"
    texts = [e.delta for e in evs if hasattr(e, "delta")]
    assert texts == ["Hel", "lo"]
    ends = [e for e in evs if isinstance(e, MessageEnd)]
    assert len(ends) == 1
    assert ends[0].stop_reason == StopReason.end_turn
    assert ends[0].usage.output_tokens == 2


def test_stream_tool_use_and_completed() -> None:
    dec = ResponsesStreamDecoder()
    evs = []
    evs += dec.decode_event({"type": "response.created", "response": {"model": "m"}})
    evs += dec.decode_event({
        "type": "response.output_item.added",
        "output_item": {"type": "function_call", "call_id": "fc1", "name": "bash"},
    })
    evs += dec.decode_event({"type": "response.function_call_arguments.delta",
                             "item_id": "fc1", "delta": '{"command"'})
    evs += dec.decode_event({
        "type": "response.completed",
        "response": {"status": "completed",
                     "output": [{"type": "function_call", "call_id": "fc1"}]},
    })
    starts = [e for e in evs if isinstance(e, MessageStart)]
    tool_starts = [e for e in evs if isinstance(e, MessageEnd) and e.stop_reason == StopReason.tool_use]
    args = [e for e in evs if hasattr(e, "id") and hasattr(e, "delta")]
    assert len(starts) == 1
    assert len(tool_starts) == 1
    assert args[0].id == "fc1"
    assert args[0].delta == '{"command"'


def test_stream_failed_raises() -> None:
    import pytest
    from vague_code.agent.ir import StreamDisconnect
    dec = ResponsesStreamDecoder()
    with pytest.raises(StreamDisconnect):
        dec.decode_event({"type": "response.failed", "error": {"message": "boom"}})

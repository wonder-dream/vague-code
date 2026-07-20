from __future__ import annotations

from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletion

from src.agent.ir import (
    Block,
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


def encode_request(
    messages: list[Message],
    tools: list[ToolSpec] | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    if not messages:
        raise ValueError("messages 不能为空")
    wire_messages: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "assistant":
            wire_messages.append(_encode_assistant(msg))
        elif msg.role == "user":
            wire_messages.extend(_encode_user(msg))
        else:
            raise ValueError(f"unsupported role: {msg.role}")
    body: dict[str, Any] = {"messages": wire_messages}
    if tools:
        body["tools"] = [t.to_openai_tool() for t in tools]
    if config:
        body.update(config)
    return body


def _encode_assistant(msg: Message) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            text_parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {"name": block.name, "arguments": _dump_json(block.input)},
            })
        elif isinstance(block, ThinkingBlock):
            continue
        else:
            raise ValueError(f"unexpected block in assistant message: {type(block).__name__}")
    result: dict[str, Any] = {"role": "assistant"}
    if text_parts:
        result["content"] = "".join(text_parts)
    if tool_calls:
        result["tool_calls"] = tool_calls
    if not text_parts and not tool_calls:
        raise ValueError("assistant message has no text or tool_use after dropping thinking blocks")
    return result


def _encode_user(msg: Message) -> list[dict[str, Any]]:
    segments: list[list[Block]] = []
    current: list[Block] = []
    for block in msg.content:
        if isinstance(block, ThinkingBlock):
            continue
        if not current or isinstance(block, type(current[-1])):
            current.append(block)
        else:
            segments.append(current)
            current = [block]
    if current:
        segments.append(current)

    wire: list[dict[str, Any]] = []
    for seg in segments:
        first = seg[0]
        if isinstance(first, ToolResultBlock):
            seg_tr = cast(list[ToolResultBlock], seg)
            for tr in seg_tr:
                if not tr.tool_use_id:
                    raise ValueError("ToolResultBlock missing tool_use_id")
                wire.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": tr.content,
                })
        elif isinstance(first, TextBlock):
            seg_text = cast(list[TextBlock], seg)
            text = "".join(b.text for b in seg_text)
            wire.append({"role": "user", "content": text})
        else:
            raise ValueError(f"unexpected block type in user message segment: {type(first).__name__}")
    return wire


def _dump_json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def decode_response(response_dict: dict[str, Any]) -> ModelResponse:
    choice = response_dict["choices"][0]
    msg_dict = choice["message"]

    blocks: list[Block] = []
    if content := msg_dict.get("content"):
        blocks.append(TextBlock(text=content))
    if reasoning := msg_dict.get("reasoning_content"):
        blocks.append(ThinkingBlock(text=reasoning))
    if tool_calls := msg_dict.get("tool_calls"):
        for tc in tool_calls:
            blocks.append(ToolUseBlock(
                id=tc["id"],
                name=tc["function"]["name"],
                input=json_loads(args) if (args := tc["function"].get("arguments")) else {},
            ))

    stop_reason = _decode_stop_reason(choice["finish_reason"])
    usage = _decode_usage(response_dict.get("usage", {}))
    return ModelResponse(
        message=Message(role="assistant", content=blocks),
        stop_reason=stop_reason,
        usage=usage,
    )


def _decode_stop_reason(finish_reason: str | None) -> StopReason:
    mapping: dict[str, StopReason] = {
        "stop": StopReason.end_turn,
        "tool_calls": StopReason.tool_use,
        "length": StopReason.max_tokens,
        "content_filter": StopReason.content_filter,
    }
    if finish_reason is None:
        return StopReason.stop_sequence
    return mapping.get(finish_reason, StopReason.stop_sequence)


def _decode_usage(usage_dict: dict[str, Any]) -> NormalizedUsage:
    cache_read = 0
    if details := usage_dict.get("prompt_tokens_details"):
        cache_read = details.get("cached_tokens", 0)
    return NormalizedUsage(
        input_tokens=usage_dict.get("prompt_tokens", 0),
        output_tokens=usage_dict.get("completion_tokens", 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=0,
    )


def json_loads(s: str) -> Any:
    import json
    return json.loads(s)


def complete(
    client: OpenAI,
    messages: list[Message],
    tools: list[ToolSpec] | None = None,
    config: dict | None = None,
) -> ModelResponse:
    body = encode_request(messages, tools, config)
    model = (config or {}).pop("model", "deepseek-chat") or "deepseek-chat"
    body["model"] = model
    raw: ChatCompletion = client.chat.completions.create(**body)
    raw_dict = raw.model_dump(mode="json")
    return decode_response(raw_dict)

from __future__ import annotations

import json
from typing import Any, cast

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

ALLOWED_CONFIG_KEYS = frozenset({"temperature", "max_tokens", "top_p", "stop", "stream", "model", "frequency_penalty", "presence_penalty"})


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
    if isinstance(config, dict):
        body.update({k: v for k, v in config.items() if k in ALLOWED_CONFIG_KEYS})
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
    if not wire:
        raise ValueError("user message content is empty after dropping thinking blocks")
    return wire


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def decode_response(response_dict: dict[str, Any]) -> ModelResponse:
    if not isinstance(response_dict, dict):
        raise ValueError(f"decode_response expected dict, got {type(response_dict).__name__}")

    choices = response_dict.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response 'choices' missing, empty, or not a list")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError(f"choice[0] is not a dict, got {type(choice).__name__}")

    msg_dict = choice.get("message")
    if not isinstance(msg_dict, dict):
        msg_dict = {}

    blocks: list[Block] = []
    content_val = msg_dict.get("content")
    if content_val:
        blocks.append(TextBlock(text=content_val))

    reasoning_val = msg_dict.get("reasoning_content")
    if reasoning_val:
        blocks.append(ThinkingBlock(text=reasoning_val))

    tool_calls = msg_dict.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id", "")
            if not tc_id:
                continue
            func = tc.get("function")
            if not isinstance(func, dict):
                continue
            tc_name = func.get("name", "")
            if not tc_name:
                continue
            args_raw = func.get("arguments", "{}")
            if isinstance(args_raw, str):
                try:
                    parsed = json_loads(args_raw)
                except json.JSONDecodeError:
                    parsed = {}
            elif isinstance(args_raw, dict):
                parsed = args_raw
            else:
                parsed = {}
            blocks.append(ToolUseBlock(id=tc_id, name=tc_name, input=parsed))

    if not blocks:
        blocks.append(TextBlock(text=""))

    finish_reason = choice.get("finish_reason")
    stop_reason = _decode_stop_reason(finish_reason)

    usage_raw = response_dict.get("usage")
    if isinstance(usage_raw, dict):
        usage = _decode_usage(usage_raw)
    else:
        usage = NormalizedUsage()

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
        return StopReason.unknown
    return mapping.get(finish_reason, StopReason.unknown)


def _decode_usage(usage_dict: dict[str, Any]) -> NormalizedUsage:
    cache_read = 0
    details = usage_dict.get("prompt_tokens_details")
    if isinstance(details, dict):
        cache_read = details.get("cached_tokens", 0)
    return NormalizedUsage(
        input_tokens=usage_dict.get("prompt_tokens", 0),
        output_tokens=usage_dict.get("completion_tokens", 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=0,
    )


def json_loads(s: str) -> Any:
    return json.loads(s)

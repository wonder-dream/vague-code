"""OpenAI Responses API codec（ADR-0034）。

IR → Responses 协议的双向映射 + 流式事件解码。协议形态（openai SDK ≥2.46）：
- input items: {role, content} / {type: "function_call", call_id, name, arguments}
  / {type: "function_call_output", call_id, output}；system → 顶层 instructions
- tools: {type: "function", name, description, parameters}
- 参数：max_output_tokens（非 max_tokens）
- 流式事件（SDK 对象 → model_dump dict）：
  response.created / response.in_progress / response.output_item.added /
  response.content_part.delta / response.output_text.delta /
  response.function_call_arguments.delta / response.completed / response.failed
"""

from __future__ import annotations

import json
from typing import Any, cast

from vague_code.agent.ir import (
    ArgsDelta,
    Block,
    Message,
    MessageEnd,
    MessageStart,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    StreamDisconnect,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseStart,
)

ALLOWED_CONFIG_KEYS = frozenset({"temperature", "max_output_tokens", "top_p", "stream", "model", "instructions", "store"})


def encode_request(
    messages: list[Message],
    tools: list[ToolSpec] | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    if not messages:
        raise ValueError("messages 不能为空")

    instructions: list[str] = []
    items: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
            if text.strip():
                instructions.append(text)
            continue

        text_parts: list[str] = []
        tool_results: list[ToolResultBlock] = []
        tool_calls: list[ToolUseBlock] = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, ToolResultBlock):
                tool_results.append(block)
            elif isinstance(block, ToolUseBlock):
                tool_calls.append(block)
            elif isinstance(block, ThinkingBlock):
                continue  # Responses 不支持回传 reasoning，丢弃
            else:
                raise ValueError(f"unexpected block: {type(block).__name__}")

        if msg.role == "assistant":
            if text_parts:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "".join(text_parts)}],
                })
            for call in tool_calls:
                items.append({
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.input, ensure_ascii=False),
                })
        else:  # user
            # 按原始 block 顺序分段（连续同类合并），保持 tool_result 相对 text 的位置
            segments: list[list[Block]] = []
            current: list[Block] = []
            for block in msg.content:
                if isinstance(block, ThinkingBlock):
                    continue
                if not current or type(block) is type(current[-1]):
                    current.append(block)
                else:
                    segments.append(current)
                    current = [block]
            if current:
                segments.append(current)
            for seg in segments:
                if isinstance(seg[0], ToolResultBlock):
                    seg_tr = cast(list[ToolResultBlock], seg)
                    for tr in seg_tr:
                        if not tr.tool_use_id:
                            raise ValueError("ToolResultBlock missing tool_use_id")
                        items.append({
                            "type": "function_call_output",
                            "call_id": tr.tool_use_id,
                            "output": tr.content,
                        })
                else:
                    seg_text = cast(list[TextBlock], seg)
                    items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": "".join(
                            b.text for b in seg_text)}],
                    })

    body: dict[str, Any] = {}
    if instructions:
        body["instructions"] = "\n\n".join(instructions)
    body["input"] = items
    if tools:
        body["tools"] = [
            {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ]
    if isinstance(config, dict):
        body.update({k: v for k, v in config.items() if k in ALLOWED_CONFIG_KEYS})
        # Chat Completions 风格 max_tokens → max_output_tokens
        if "max_tokens" in config and "max_output_tokens" not in body:
            body["max_output_tokens"] = config["max_tokens"]
            body.pop("max_tokens", None)
    return body


def _stop_reason(status: str | None, has_tool_calls: bool) -> StopReason:
    if status == "completed":
        return StopReason.tool_use if has_tool_calls else StopReason.end_turn
    if status in ("incomplete", "cancelled"):
        return StopReason.max_tokens
    return StopReason.unknown


def _decode_usage(usage: dict | None) -> NormalizedUsage:
    if not isinstance(usage, dict):
        return NormalizedUsage()
    return NormalizedUsage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=0,
        cache_write_tokens=0,
    )


def decode_response(response_dict: dict[str, Any]) -> ModelResponse:
    if not isinstance(response_dict, dict):
        raise ValueError(f"decode_response expected dict, got {type(response_dict).__name__}")

    blocks: list[Block] = []
    has_tool_calls = False
    for item in response_dict.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = str(part.get("text") or "")
                    if text:
                        blocks.append(TextBlock(text=text))
        elif item.get("type") == "function_call":
            has_tool_calls = True
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            if not call_id or not name:
                continue
            args_raw = item.get("arguments", "{}")
            try:
                parsed = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                parsed = {}
            blocks.append(ToolUseBlock(id=call_id, name=name, input=parsed))

    if not blocks:
        blocks.append(TextBlock(text=""))

    status = response_dict.get("status")
    return ModelResponse(
        message=Message(role="assistant", content=blocks),
        stop_reason=_stop_reason(status, has_tool_calls),
        usage=_decode_usage(response_dict.get("usage")),
    )


# ── Stream decoder ───────────────────────────────────────────────────────────

class ResponsesStreamDecoder:
    """OpenAI Responses 事件流 → StreamEvent 序列（对象 model_dump 为 dict 后喂入）。"""

    def __init__(self) -> None:
        self._model = "?"
        self._tools: dict[str, str] = {}       # call_id -> name
        self._tool_order: list[str] = []
        self._finished = False

    def decode_event(self, ev: dict) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        etype = ev.get("type")
        if etype == "response.created":
            resp = ev.get("response") or {}
            self._model = str(resp.get("model") or "?")
            out.append(MessageStart(model=self._model))
        elif etype == "response.output_item.added":
            item = ev.get("output_item") or {}
            if item.get("type") == "function_call":
                call_id = str(item.get("call_id") or item.get("id") or "")
                name = str(item.get("name") or "tool")
                if call_id and call_id not in self._tools:
                    self._tools[call_id] = name
                    self._tool_order.append(call_id)
                    out.append(ToolUseStart(id=call_id, name=name))
        elif etype == "response.output_text.delta":
            delta = str(ev.get("delta") or "")
            if delta:
                out.append(TextDelta(delta=delta))
        elif etype == "response.function_call_arguments.delta":
            call_id = str(ev.get("item_id") or ev.get("output_index") or "")
            delta = str(ev.get("delta") or "")
            if call_id and delta:
                out.append(ArgsDelta(id=call_id, delta=delta))
        elif etype == "response.completed":
            resp = ev.get("response") or {}
            has_tool = any(
                isinstance(i, dict) and i.get("type") == "function_call"
                for i in resp.get("output") or []
            )
            out.append(MessageEnd(
                stop_reason=_stop_reason(resp.get("status"), has_tool),
                finish_reason=None,
                truncated=resp.get("status") in ("incomplete", "cancelled"),
                usage=_decode_usage(resp.get("usage")),
            ))
            self._finished = True
        elif etype == "response.failed":
            raise StreamDisconnect(f"responses stream failed: {ev.get('error')}")
        return out

    def flush(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for call_id in self._tool_order:
            out.append(ToolUseEnd(id=call_id))
        if not self._finished:
            out.append(MessageEnd(
                stop_reason=StopReason.unknown,
                finish_reason=None,
                truncated=False,
                usage=NormalizedUsage(),
            ))
        return out

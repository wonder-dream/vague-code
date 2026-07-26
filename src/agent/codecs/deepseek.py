from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from src.agent.ir import (
    ArgsDelta,
    Block,
    Message,
    MessageEnd,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    StreamDisconnect,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseStart,
)

ALLOWED_CONFIG_KEYS = frozenset({"temperature", "max_tokens", "top_p", "stop", "stream", "model", "frequency_penalty", "presence_penalty"})


def encode_request(
    messages: list[Message],
    tools: list[ToolSpec] | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    if not messages:
        raise ValueError("messages 不能为空")
    system_parts: list[str] = []
    non_system: list[Message] = []
    for msg in messages:
        if msg.role == "system":
            text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
            if text.strip():
                system_parts.append(text)
        elif msg.role == "assistant":
            non_system.append(msg)
        elif msg.role == "user":
            non_system.append(msg)
        else:
            raise ValueError(f"unsupported role: {msg.role}")

    wire_messages: list[dict[str, Any]] = []
    if system_parts:
        wire_messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    for msg in non_system:
        if msg.role == "assistant":
            wire_messages.append(_encode_assistant(msg))
        else:
            wire_messages.extend(_encode_user(msg))
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


# ── Stream decoder ───────────────────────────────────────────────────────────

@dataclass
class _ToolState:
    id: str | None = None
    name: str | None = None
    started: bool = False
    pending_args: list[str] = field(default_factory=list)


class DeepSeekStreamDecoder:
    """每流一个解码器，将 OpenAI-compatible SSE chunk 翻译为 StreamEvent 序列。

    状态机覆盖 thinking 边界推断（无显式 chink 时）、tool_call index→id 映射、
    finish 与 usage 分 chunk 到达的延迟发射。
    """

    def __init__(self):
        self._thinking_open = False
        self._tools: dict[int, _ToolState] = {}
        self._tool_order: list[int] = []
        self._pending_finish: str | None = None
        self._usage = NormalizedUsage()
        self._usage_received = False
        self._ended = False

    def decode_chunk(self, chunk: dict) -> list[StreamEvent]:
        out: list[StreamEvent] = []

        # Step 0 — 防御入口
        if "error" in chunk:
            err = chunk.get("error", {})
            raise StreamDisconnect(f"stream error: {err}")
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self._usage = _decode_usage(usage)
            self._usage_received = True
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return self._maybe_emit_end()
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason")

        # Step 1 — Thinking 边界
        rc = delta.get("reasoning_content")
        if rc:
            if not self._thinking_open:
                out.append(ThinkingStart())
                self._thinking_open = True
            out.append(ThinkingDelta(delta=rc))
        if self._thinking_open and not rc and (
            delta.get("content") or delta.get("tool_calls") or finish
        ):
            out.append(ThinkingEnd(signature=None))
            self._thinking_open = False

        # Step 2 — Text
        content = delta.get("content")
        if content:
            out.append(TextDelta(delta=content))

        # Step 3 — Tool calls（按 index 独立追踪）
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index")
            if idx is None:
                raise ValueError("tool_call delta missing index")
            st = self._tools.setdefault(idx, _ToolState())
            if idx not in self._tool_order:
                self._tool_order.append(idx)
            if tc.get("id"):
                st.id = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                st.name = fn["name"]
            if not st.started and st.id and st.name:
                out.append(ToolUseStart(id=st.id, name=st.name))
                st.started = True
                for frag in st.pending_args:
                    out.append(ArgsDelta(id=st.id, delta=frag))
                st.pending_args.clear()
            if "arguments" in fn and fn["arguments"] is not None:
                args_val = fn["arguments"]
                if st.started and st.id is not None:
                    out.append(ArgsDelta(id=st.id, delta=args_val))
                else:
                    st.pending_args.append(args_val)

        # Step 4 — Finish 收尾：尝试发射（usage 已在同一 chunk 或之前 chunk 到达则立即发射）
        if finish is not None:
            if self._thinking_open:
                out.append(ThinkingEnd(signature=None))
                self._thinking_open = False
            self._pending_finish = finish
            out += self._close_all_tools()
            out += self._maybe_emit_end()

        return out

    def flush(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        if self._thinking_open:
            out.append(ThinkingEnd(signature=None))
            self._thinking_open = False
        out += self._close_all_tools()
        emitted = self._maybe_emit_end()
        out += emitted
        if not self._ended:
            self._ended = True
            rest = _decode_stop_reason(self._pending_finish) if self._pending_finish else StopReason.unknown
            out.append(MessageEnd(
                stop_reason=rest,
                finish_reason=self._pending_finish,
                truncated=(self._pending_finish == "length") if self._pending_finish else False,
                usage=self._usage,
            ))
        return out

    # ── internal helpers ──────────────────────────────────────────────────────

    def _maybe_emit_end(self) -> list[StreamEvent]:
        # 仅在 finish 已就绪且 usage 已到达（或 flush 强制）时发射
        if self._ended or self._pending_finish is None:
            return []
        if not self._usage_received:
            return []  # 等 usage chunk
        self._ended = True
        finish = self._pending_finish
        return [MessageEnd(
            stop_reason=_decode_stop_reason(finish),
            finish_reason=finish,
            truncated=finish == "length",
            usage=self._usage,
        )]

    def _close_all_tools(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for idx in self._tool_order:
            st = self._tools[idx]
            if st.started and st.id is not None:
                out.append(ToolUseEnd(id=st.id))
                st.started = False
        return out

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent.ir import (
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
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseStart,
)

ALLOWED_CONFIG_KEYS = frozenset({"temperature", "top_p", "max_tokens", "stop_sequences", "metadata", "thinking"})

STOP_REASON_MAP: dict[str, StopReason] = {
    "end_turn": StopReason.end_turn,
    "max_tokens": StopReason.max_tokens,
    "tool_use": StopReason.tool_use,
    "stop_sequence": StopReason.stop_sequence,
    "refusal": StopReason.content_filter,
}


def encode_request(
    messages: list[Message],
    tools: list[ToolSpec] | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    if not messages:
        raise ValueError("messages cannot be empty")

    system_parts: list[str] = []
    non_system: list[Message] = []
    for msg in messages:
        if msg.role == "system":
            system_parts.extend(
                b.text for b in msg.content if isinstance(b, TextBlock)
            )
        else:
            non_system.append(msg)

    merged = _merge_consecutive_same_role(non_system)
    if not merged or merged[0].role != "user":
        raise ValueError("first message must have role 'user'")

    wire_messages: list[dict[str, Any]] = []
    for msg in merged:
        if msg.role == "assistant":
            wire_messages.append(_encode_assistant(msg))
        elif msg.role == "user":
            wire_messages.append(_encode_user(msg))
        else:
            raise ValueError(f"unsupported role: {msg.role}")

    body: dict[str, Any] = {"messages": wire_messages}
    if system_parts:
        body["system"] = "\n".join(system_parts)
    if tools:
        body["tools"] = [t.to_anthropic_tool() for t in tools]
    if isinstance(config, dict):
        body.update({k: v for k, v in config.items() if k in ALLOWED_CONFIG_KEYS})
    return body


def _merge_consecutive_same_role(messages: list[Message]) -> list[Message]:
    if not messages:
        return []
    result: list[Message] = []
    current = messages[0]
    for next_msg in messages[1:]:
        if next_msg.role == current.role:
            current = Message(
                role=current.role,
                content=current.content + next_msg.content,
            )
        else:
            result.append(current)
            current = next_msg
    result.append(current)
    return result


def _encode_assistant(msg: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        elif isinstance(block, ThinkingBlock):
            if block.signature:
                blocks.append({
                    "type": "thinking",
                    "thinking": block.text,
                    "signature": block.signature,
                })
        else:
            raise ValueError(f"unexpected block in assistant message: {type(block).__name__}")
    if not blocks:
        raise ValueError("assistant message has no valid content blocks")
    return {"role": "assistant", "content": blocks}


def _encode_user(msg: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolResultBlock):
            content = block.content
            if not content:
                content = "(empty)"
            blocks.append({
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": content,
                "is_error": block.is_error,
            })
        elif isinstance(block, ThinkingBlock):
            continue
        else:
            raise ValueError(f"unexpected block in user message: {type(block).__name__}")
    if not blocks:
        raise ValueError("user message content is empty after processing")
    return {"role": "user", "content": blocks}


def decode_response(response_dict: dict[str, Any]) -> ModelResponse:
    blocks: list[Block] = []
    for cb in response_dict.get("content") or []:
        if not isinstance(cb, dict):
            continue
        t = cb.get("type")
        if t == "text":
            blocks.append(TextBlock(text=cb.get("text", "")))
        elif t == "tool_use":
            blocks.append(ToolUseBlock(
                id=cb.get("id", ""),
                name=cb.get("name", ""),
                input=cb.get("input", {}),
            ))
        elif t == "thinking":
            blocks.append(ThinkingBlock(
                text=cb.get("thinking", ""),
                signature=cb.get("signature"),
            ))

    if not blocks:
        blocks.append(TextBlock(text=""))

    stop_reason_str: str | None = response_dict.get("stop_reason")
    stop_reason = STOP_REASON_MAP.get(stop_reason_str, StopReason.unknown)  # type: ignore[arg-type]

    usage_raw = response_dict.get("usage") or {}
    usage = NormalizedUsage(
        input_tokens=usage_raw.get("input_tokens", 0),
        output_tokens=usage_raw.get("output_tokens", 0),
        cache_read_tokens=usage_raw.get("cache_read_input_tokens", 0),
        cache_write_tokens=usage_raw.get("cache_creation_input_tokens", 0),
    )

    return ModelResponse(
        message=Message(role="assistant", content=blocks),
        stop_reason=stop_reason,
        usage=usage,
    )


# ── Stream decoder ───────────────────────────────────────────────────────────

@dataclass
class _BlockState:
    type: str
    id: str | None = None
    sig_frags: list[str] = field(default_factory=list)


SKIP_BLOCK_TYPES = frozenset({
    "redacted_thinking", "server_tool_use", "web_search_tool_result",
    "web_fetch_tool_result", "code_execution_tool_result",
    "bash_code_execution_tool_result", "text_editor_code_execution_tool_result",
    "tool_search_tool_result", "container_upload",
})


class AnthropicStreamDecoder:
    def __init__(self):
        self._blocks: dict[int, _BlockState] = {}
        self._input_tokens: int = 0
        self._usage = NormalizedUsage()
        self._stop_reason: StopReason | None = None
        self._truncated: bool = False
        self._ended: bool = False

    def decode_event(self, event: dict) -> list[StreamEvent]:
        if not isinstance(event, dict):
            return []
        out: list[StreamEvent] = []
        et = event.get("type")

        if et == "message_start":
            msg = event.get("message") or {}
            out.append(MessageStart(model=msg.get("model", "?")))
            usage_raw = msg.get("usage") or {}
            self._input_tokens = usage_raw.get("input_tokens", 0)

        elif et == "content_block_start":
            idx = event.get("index", 0)
            cb = event.get("content_block") or {}
            cbt = cb.get("type")
            if cbt == "tool_use":
                tid = cb.get("id", "")
                tname = cb.get("name", "")
                self._blocks[idx] = _BlockState(type="tool_use", id=tid)
                out.append(ToolUseStart(id=tid, name=tname))
            elif cbt == "thinking":
                self._blocks[idx] = _BlockState(type="thinking")
                out.append(ThinkingStart())
            elif cbt in SKIP_BLOCK_TYPES:
                self._blocks[idx] = _BlockState(type="ignore")
            else:
                self._blocks[idx] = _BlockState(type="text")

        elif et == "content_block_delta":
            idx = event.get("index", 0)
            st = self._blocks.get(idx)
            if st is None:
                return []
            delta = event.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                out.append(TextDelta(delta=delta.get("text", "")))
            elif dt == "input_json_delta":
                pid = st.id if st.id else ""
                out.append(ArgsDelta(id=pid, delta=delta.get("partial_json", "")))
            elif dt == "thinking_delta":
                out.append(ThinkingDelta(delta=delta.get("thinking", "")))
            elif dt == "signature_delta":
                sig = delta.get("signature", "")
                if sig:
                    st.sig_frags.append(sig)

        elif et == "content_block_stop":
            idx = event.get("index", 0)
            st = self._blocks.pop(idx, None)
            if st is None:
                return []
            if st.type == "thinking":
                signature = "".join(st.sig_frags) or None
                out.append(ThinkingEnd(signature=signature))
            elif st.type == "tool_use":
                out.append(ToolUseEnd(id=st.id or ""))

        elif et == "message_delta":
            delta = event.get("delta") or {}
            sr = delta.get("stop_reason")
            if sr:
                self._stop_reason = STOP_REASON_MAP.get(sr, StopReason.unknown)
            self._truncated = (sr == "max_tokens")
            usage_raw = event.get("usage") or {}
            self._usage = NormalizedUsage(
                input_tokens=self._input_tokens,
                output_tokens=usage_raw.get("output_tokens", 0),
                cache_read_tokens=usage_raw.get("cache_read_input_tokens", 0),
                cache_write_tokens=usage_raw.get("cache_creation_input_tokens", 0),
            )

        elif et == "error":
            err = event.get("error") or {}
            etype = err.get("type", "unknown")
            msg = err.get("message", str(err))
            raise StreamDisconnect(f"API streaming error: [{etype}] {msg}")

        elif et == "message_stop":
            out.append(MessageEnd(
                stop_reason=self._stop_reason or StopReason.unknown,
                truncated=self._truncated,
                usage=self._usage,
            ))
            self._ended = True

        return out

    def flush(self) -> list[StreamEvent]:
        out: list[StreamEvent] = []
        for idx in sorted(self._blocks.keys(), reverse=True):
            st = self._blocks.pop(idx)
            if st.type == "thinking":
                signature = "".join(st.sig_frags) or None
                out.append(ThinkingEnd(signature=signature))
            elif st.type == "tool_use":
                out.append(ToolUseEnd(id=st.id or ""))
        if not self._ended:
            out.append(MessageEnd(
                stop_reason=self._stop_reason or StopReason.unknown,
                truncated=self._truncated,
                usage=self._usage,
            ))
            self._ended = True
        return out

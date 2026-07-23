from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol


BlockType = Literal["text", "thinking", "tool_use", "tool_result"]


@dataclass
class TextBlock:
    text: str
    meta: dict = field(default_factory=dict)

    @staticmethod
    def _type() -> BlockType:
        return "text"

    def to_dict(self) -> dict:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingBlock:
    text: str
    signature: str | None = None
    meta: dict = field(default_factory=dict)

    @staticmethod
    def _type() -> BlockType:
        return "thinking"

    def to_dict(self) -> dict:
        d: dict = {"type": "thinking", "text": self.text}
        if self.signature is not None:
            d["signature"] = self.signature
        return d


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.input, dict):
            raise ValueError(f"ToolUseBlock.input must be dict, got {type(self.input).__name__}")
        if not self.id:
            raise ValueError("ToolUseBlock.id must not be empty")
        if not self.name:
            raise ValueError("ToolUseBlock.name must not be empty")

    @staticmethod
    def _type() -> BlockType:
        return "tool_use"

    def to_dict(self) -> dict:
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_use_id:
            raise ValueError("ToolResultBlock.tool_use_id must not be empty")

    @staticmethod
    def _type() -> BlockType:
        return "tool_result"

    def to_dict(self) -> dict:
        return {"type": "tool_result", "tool_use_id": self.tool_use_id, "content": self.content, "is_error": self.is_error}


Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: list[Block]

    def __init__(self, role: Literal["user", "assistant"], content: str | list[Block]):
        self.role = role
        if content is None:
            raise ValueError("content must not be None")
        if isinstance(content, str):
            self.content = [TextBlock(text=content)]
        else:
            if not content:
                raise ValueError("content list must not be empty")
            self.content = content

    def to_dict(self) -> dict:
        return {"role": self.role, "content": [b.to_dict() for b in self.content]}


class StopReason(Enum):
    end_turn = "end_turn"
    max_tokens = "max_tokens"
    stop_sequence = "stop_sequence"
    tool_use = "tool_use"
    content_filter = "content_filter"
    unknown = "unknown"


@dataclass
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for field_name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"NormalizedUsage.{field_name} must be >= 0, got {value}")

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ModelResponse:
    message: Message
    stop_reason: StopReason
    usage: NormalizedUsage

    def to_dict(self) -> dict:
        return {
            "message": self.message.to_dict(),
            "stop_reason": self.stop_reason.value,
            "usage": self.usage.to_dict(),
        }


# ── StreamEvent types ───────────────────────────────────────────────────────

@dataclass
class MessageStart:
    model: str
    def to_dict(self) -> dict:
        return {"stream_type": "message_start", "model": self.model}

@dataclass
class ThinkingStart:
    def to_dict(self) -> dict:
        return {"stream_type": "thinking_start"}

@dataclass
class ThinkingDelta:
    delta: str
    def to_dict(self) -> dict:
        return {"stream_type": "thinking_delta", "delta": self.delta}

@dataclass
class ThinkingEnd:
    signature: str | None
    def to_dict(self) -> dict:
        d: dict = {"stream_type": "thinking_end"}
        if self.signature is not None:
            d["signature"] = self.signature
        return d

@dataclass
class TextDelta:
    delta: str
    def to_dict(self) -> dict:
        return {"stream_type": "text_delta", "delta": self.delta}

@dataclass
class ToolUseStart:
    id: str
    name: str
    def to_dict(self) -> dict:
        return {"stream_type": "tool_use_start", "id": self.id, "name": self.name}

@dataclass
class ArgsDelta:
    id: str
    delta: str
    def to_dict(self) -> dict:
        return {"stream_type": "args_delta", "id": self.id, "delta": self.delta}

@dataclass
class ToolUseEnd:
    id: str
    def to_dict(self) -> dict:
        return {"stream_type": "tool_use_end", "id": self.id}

@dataclass
class MessageEnd:
    stop_reason: StopReason
    finish_reason: str | None = None
    truncated: bool = False
    usage: NormalizedUsage | None = None

    def to_dict(self) -> dict:
        return {
            "stream_type": "message_end",
            "stop_reason": self.stop_reason.value,
            "finish_reason": self.finish_reason,
            "truncated": self.truncated,
            "usage": self.usage.to_dict() if self.usage else NormalizedUsage().to_dict(),
        }

StreamEvent = MessageStart | ThinkingStart | ThinkingDelta | ThinkingEnd | TextDelta | ToolUseStart | ArgsDelta | ToolUseEnd | MessageEnd


class StreamDisconnect(Exception):
    """Raised when the LLM stream ends abnormally (disconnect, no MessageEnd, etc.)."""


# ── StreamEventVisitor ──────────────────────────────────────────────────────

class StreamEventVisitor(Protocol):
    def message_start(self, ev: MessageStart) -> None: ...
    def thinking_start(self, ev: ThinkingStart) -> None: ...
    def thinking_delta(self, ev: ThinkingDelta) -> None: ...
    def thinking_end(self, ev: ThinkingEnd) -> None: ...
    def text_delta(self, ev: TextDelta) -> None: ...
    def tool_use_start(self, ev: ToolUseStart) -> None: ...
    def args_delta(self, ev: ArgsDelta) -> None: ...
    def tool_use_end(self, ev: ToolUseEnd) -> None: ...
    def message_end(self, ev: MessageEnd) -> None: ...


def dispatch_event(ev: StreamEvent, v: StreamEventVisitor) -> None:
    if isinstance(ev, MessageStart):
        v.message_start(ev)
    elif isinstance(ev, ThinkingStart):
        v.thinking_start(ev)
    elif isinstance(ev, ThinkingDelta):
        v.thinking_delta(ev)
    elif isinstance(ev, ThinkingEnd):
        v.thinking_end(ev)
    elif isinstance(ev, TextDelta):
        v.text_delta(ev)
    elif isinstance(ev, ToolUseStart):
        v.tool_use_start(ev)
    elif isinstance(ev, ArgsDelta):
        v.args_delta(ev)
    elif isinstance(ev, ToolUseEnd):
        v.tool_use_end(ev)
    elif isinstance(ev, MessageEnd):
        v.message_end(ev)


class NullVisitor:
    def __getattr__(self, name: str):
        if name.startswith("message_") or name.startswith("thinking_") or name.startswith("text_") or name.startswith("tool_use_") or name.startswith("args_"):
            return lambda ev: None
        raise AttributeError(name)

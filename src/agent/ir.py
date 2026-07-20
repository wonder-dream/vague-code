from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


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
    meta: dict = field(default_factory=dict)

    @staticmethod
    def _type() -> BlockType:
        return "thinking"

    def to_dict(self) -> dict:
        return {"type": "thinking", "text": self.text}


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    meta: dict = field(default_factory=dict)

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
        if isinstance(content, str):
            self.content = [TextBlock(text=content)]
        else:
            self.content = content

    def to_dict(self) -> dict:
        return {"role": self.role, "content": [b.to_dict() for b in self.content]}


class StopReason(Enum):
    end_turn = "end_turn"
    max_tokens = "max_tokens"
    stop_sequence = "stop_sequence"
    tool_use = "tool_use"
    content_filter = "content_filter"


@dataclass
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

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

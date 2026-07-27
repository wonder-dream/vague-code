from __future__ import annotations

import json

from src.agent.ir import (
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 64_000,
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-5": 200_000,
}

_SENDS_THINKING_PREFIXES: tuple[str, ...] = ("claude-",)


def should_skip_thinking(model: str) -> bool:
    """Return True if the model's codec drops ThinkingBlock on the wire (so token budget skips them).
    Return False for models whose codec sends ThinkingBlock (e.g. Anthropic claude)."""
    for prefix in _SENDS_THINKING_PREFIXES:
        if model.startswith(prefix):
            return False
    return True


_ENC: object | None = None


def _get_enc():
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENC = False
    return _ENC if _ENC is not False else None


def count_tokens(
    messages: list,
    tools: list[ToolSpec] | None = None,
    skip_thinking: bool = False,
) -> int:
    enc = _get_enc()
    if enc is not None:
        return _count_precise(messages, tools, enc, skip_thinking)
    return _count_rough(messages, tools, skip_thinking)


def _count_precise(
    messages: list,
    tools: list[ToolSpec] | None,
    enc,
    skip_thinking: bool = False,
) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(enc.encode(block.text))
            elif isinstance(block, ThinkingBlock):
                if not skip_thinking:
                    total += len(enc.encode(block.text))
            elif isinstance(block, ToolUseBlock):
                total += len(enc.encode(block.name))
                total += len(enc.encode(json.dumps(block.input)))
            elif isinstance(block, ToolResultBlock):
                total += len(enc.encode(block.content))
    if tools:
        for t in tools:
            if t is None:
                continue
            total += len(enc.encode(t.name))
            total += len(enc.encode(t.description))
            total += len(enc.encode(json.dumps(t.parameters)))
    return total


def _count_rough(
    messages: list,
    tools: list[ToolSpec] | None = None,
    skip_thinking: bool = False,
) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(block.text) // 4
            elif isinstance(block, ThinkingBlock):
                if not skip_thinking:
                    total += len(block.text) // 4
            elif isinstance(block, ToolUseBlock):
                total += len(block.name) // 4
                total += len(json.dumps(block.input)) // 4
            elif isinstance(block, ToolResultBlock):
                total += len(block.content) // 4
    if tools:
        for t in tools:
            if t is None:
                continue
            total += len(t.name) // 4
            total += len(t.description) // 4
            total += len(json.dumps(t.parameters)) // 4
    return total


def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    window = CONTEXT_WINDOWS.get(model, 64_000)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget

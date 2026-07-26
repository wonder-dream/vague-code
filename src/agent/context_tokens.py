from __future__ import annotations

import json

import tiktoken

from src.agent.ir import TextBlock, ToolSpec


CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 64_000,
    "claude-opus-4-8": 200_000,
}

_ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(messages: list, tools: list[ToolSpec] | None = None) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(_ENC.encode(block.text))
    if tools:
        for t in tools:
            total += len(_ENC.encode(t.description))
            total += len(_ENC.encode(json.dumps(t.parameters)))
    return total


def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    window = CONTEXT_WINDOWS.get(model, 64_000)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget

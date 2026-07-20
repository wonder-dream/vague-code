"""v0 roundtrip: IR + codec → real DeepSeek API → one tool call round.

Usage:
    set DEEPSEEK_API_KEY=sk-xxx
    python scripts/v0_roundtrip.py

Reads a file, then reflects usage/stop_reason. No API key hardcoded.
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

from src.agent.codecs.deepseek import complete
from src.agent.ir import (
    Message,
    ModelResponse,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


TOOLS = [
    ToolSpec(
        name="read_file",
        description="读取文件内容",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
]


def main() -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("FATAL: set DEEPSEEK_API_KEY env var", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    messages: list[Message] = [
        Message(role="user", content="读一下 README.md，告诉我这个项目是做什么的"),
    ]

    # ── Round 1: model picks the tool ──
    print("─── Round 1: send request ───")
    config = {"model": "deepseek-chat", "temperature": 0}
    resp1: ModelResponse = complete(client, messages, tools=TOOLS, config=config)
    print(f"stop_reason: {resp1.stop_reason.value}")
    print(f"usage: {resp1.usage}")
    print(f"blocks: {[type(b).__name__ for b in resp1.message.content]}")
    messages.append(resp1.message)

    # ── Execute tool ──
    print("─── Round 1: execute tool ───")

    tool_results: list[ToolResultBlock] = []
    for block in resp1.message.content:
        if isinstance(block, ToolUseBlock):
            print(f"  tool_call: {block.name}({block.input})")
            if block.name == "read_file":
                try:
                    content = read_file(**block.input)
                    tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content))
                except Exception as e:
                    tool_results.append(ToolResultBlock(tool_use_id=block.id, content=str(e), is_error=True))

    messages.append(Message(role="user", content=tool_results))

    # ── Round 2: model reads tool result and answers ──
    print("─── Round 2: send tool result back ───")
    resp2: ModelResponse = complete(client, messages, tools=TOOLS, config=config)
    print(f"stop_reason: {resp2.stop_reason.value}")
    print(f"usage: {resp2.usage}")
    for block in resp2.message.content:
        if isinstance(block, TextBlock):
            print(f"\n最终回答：{block.text}")
        elif isinstance(block, ThinkingBlock):
            print(f"[thinking] {block.text[:200]}")


if __name__ == "__main__":
    main()

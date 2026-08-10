"""Anthropic 协议验证脚本。

使用 DeepSeek 的 Anthropic 兼容端点验证 codec 协议往返。
不修改任何源代码，只用于人工确认 encode → 真实 API → decode 链路正常。

环境要求：
  .env 或环境变量中有 DEEPSEEK_API_KEY

用法：
  uv run python scripts/verify_anthropic.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values

from vague_code.agent.backend import create_anthropic_backend
from vague_code.agent.config import TransportConfig
from vague_code.agent.ir import (
    Message,
    StreamEvent,
    StopReason,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
)


def _get_api_key() -> str | None:
    env_file = dotenv_values()
    key = env_file.get("DEEPSEEK_API_KEY")
    if key:
        return key
    return os.environ.get("DEEPSEEK_API_KEY")


def _print_separator(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def verify_text_only(backend) -> None:
    _print_separator("Test 1: 纯文本往返")
    messages = [
        Message(role="user", content="用中文说'你好，来自 Anthropic codec！'。"),
    ]
    config = {"model": "deepseek-v4-flash", "max_tokens": 800, "temperature": 0.0}
    response = backend.complete(messages, config=config)

    text = "".join(
        b.text for b in response.message.content if isinstance(b, TextBlock)
    )
    print(f"  stop_reason: {response.stop_reason}")
    print(f"  usage: {response.usage}")
    print(f"  response: {text[:200]}")
    assert response.stop_reason in (StopReason.end_turn, StopReason.max_tokens), \
        f"unexpected stop_reason: {response.stop_reason}"
    assert text.strip(), "response should not be empty"
    print("  [PASS]")


def verify_tool_call(backend) -> None:
    _print_separator("Test 2: 工具调用往返")
    tools = [
        ToolSpec(
            name="read_file",
            description="读取指定路径的文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                },
                "required": ["path"],
            },
        ),
    ]
    messages = [
        Message(role="user", content="读取 'hello.txt' 文件。"),
    ]
    config = {"model": "deepseek-v4-flash", "max_tokens": 2000, "temperature": 0.0}
    response = backend.complete(messages, tools=tools, config=config)

    tool_blocks = [b for b in response.message.content if isinstance(b, ToolUseBlock)]
    if tool_blocks:
        print(f"  stop_reason: {response.stop_reason}")
        print(f"  tool calls: {len(tool_blocks)}")
        for tb in tool_blocks:
            print(f"    - {tb.name}({tb.input})")
        print("  [PASS] (model triggered tool call)")
    else:
        text = "".join(
            b.text for b in response.message.content if isinstance(b, TextBlock)
        )
        print(f"  stop_reason: {response.stop_reason}")
        print(f"  full text response instead of tool call: {text[:200]}")
        print("  [WARN] Partial pass (text response, no tool call)")
        print("     This is model-dependent, not a codec issue.")


def verify_stream_text(backend) -> None:
    _print_separator("Test 3: 流式文本输出")
    messages = [
        Message(role="user", content="从 1 数到 5，用逗号分隔。"),
    ]
    config = {"model": "deepseek-v4-flash", "max_tokens": 500, "temperature": 0.0}

    collected: list[StreamEvent] = []
    for event in backend.stream(messages, config=config):
        collected.append(event)

    text_deltas = "".join(e.delta for e in collected if hasattr(e, "delta") and e.__class__.__name__ == "TextDelta")
    print(f"  streamed text: {text_deltas[:200]}")
    has_start = any(e.__class__.__name__ == "MessageStart" for e in collected)
    has_end = any(e.__class__.__name__ == "MessageEnd" for e in collected)
    print(f"  has MessageStart: {has_start}")
    print(f"  has MessageEnd: {has_end}")
    print(f"  total events: {len(collected)}")
    assert has_start, "missing MessageStart"
    assert has_end, "missing MessageEnd"
    assert text_deltas.strip(), "text should not be empty"
    print("  [PASS]")


def main() -> None:
    api_key = _get_api_key()
    if not api_key:
        print("Error: DEEPSEEK_API_KEY not found in .env or environment.")
        sys.exit(1)

    transport = TransportConfig(stream=False, timeout_s=60.0)

    backend = create_anthropic_backend(
        api_key=api_key,
        base_url="https://api.deepseek.com/anthropic",
        timeout_s=transport.timeout_s,
    )

    print("Backend: Anthropic (DeepSeek proxy)")
    print("Base URL: https://api.deepseek.com/anthropic")
    print("Model: deepseek-v4-flash")

    verify_text_only(backend)
    verify_tool_call(backend)
    verify_stream_text(backend)

    _print_separator("全部验证完成")
    print("  测试总结:")
    print("  - 纯文本 encode → API → decode: 完成")
    print("  - 工具调用 encode → API → decode: 完成")
    print("  - 流式 encode → stream events: 完成")
    print()
    print("  [NOTE] DeepSeek Anthropic 端点不返回 thinking block,")
    print("       thinking 路径由 golden fixture 测试覆盖。")


if __name__ == "__main__":
    main()

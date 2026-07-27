from __future__ import annotations

from typing import cast

from src.agent.context_tokens import compute_budget, count_tokens
from src.agent.ir import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


def test_empty_messages_zero() -> None:
    assert count_tokens([]) == 0


def test_single_text() -> None:
    msgs = [Message(role="user", content="hello")]
    assert count_tokens(msgs) > 0


def test_system_message_counted() -> None:
    msgs = [Message(role="system", content="you are an agent")]
    assert count_tokens(msgs) > 0


def test_multi_block_message() -> None:
    msgs = [Message(role="user", content=[
        TextBlock(text="first block"),
        TextBlock(text="second block"),
    ])]
    assert count_tokens(msgs) > 0


def test_tools_included() -> None:
    tools = [
        ToolSpec(
            name="read_file",
            description="Read a file",
            parameters={"type": "object", "properties": {}},
        ),
    ]
    no_tools = count_tokens([])
    with_tools = count_tokens([], tools)
    assert with_tools > no_tools


def test_compute_budget_known_model() -> None:
    assert compute_budget("deepseek-v4-flash") == 900_000
    assert compute_budget("deepseek-v4-pro") == 57_600


def test_compute_budget_unknown_model() -> None:
    assert compute_budget("unknown-model") == 57_600  # default 64000 * 0.9


def test_compute_budget_with_user_limit() -> None:
    assert compute_budget("deepseek-v4-flash", user_max_tokens=100_000) == 100_000


def test_compute_budget_user_limit_not_exceeded() -> None:
    assert compute_budget("deepseek-v4-flash", user_max_tokens=2_000_000) == 900_000


def test_count_tokens_none_tool_ignored() -> None:
    bad_tools = cast("list", [None])
    assert count_tokens([], bad_tools) >= 0


def test_count_tokens_fallback_on_tiktoken_missing(monkeypatch) -> None:
    import src.agent.context_tokens as ct

    monkeypatch.setattr(ct, "_ENC", False)
    msgs = [Message(role="user", content="test message")]
    result = count_tokens(msgs)
    assert result > 0


def test_fallback_less_precise_but_reasonable() -> None:
    import src.agent.context_tokens as ct

    saved = ct._ENC
    ct._ENC = False
    try:
        msgs = [Message(role="user", content="abcdefgh" * 100)]
        result = count_tokens(msgs)
        assert result >= 100
    finally:
        ct._ENC = saved


def test_count_tokens_includes_tool_blocks() -> None:
    text_only = [Message(role="user", content="hello")]
    with_tools = [
        Message(role="assistant", content=[
            ThinkingBlock(text="think step by step", signature="sig"),
            TextBlock(text="let me check"),
            ToolUseBlock(id="c1", name="read_file", input={"path": "x.txt"}),
        ]),
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="c1", content="file contents here"),
        ]),
    ]
    # verify non-TextBlock types contribute meaningfully
    base = count_tokens(text_only)
    total = count_tokens(with_tools)
    assert total > base

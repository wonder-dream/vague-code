from __future__ import annotations

from src.agent.context_compress import microcompact
from src.agent.context_tokens import count_tokens
from src.agent.ir import (
    Block,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _tool_result_pair(tool_id: str, content: str) -> list[Message]:
    return [
        Message(role="assistant", content=[ToolUseBlock(id=tool_id, name="bash", input={"cmd": "test"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id=tool_id, content=content)]),
    ]


SHORT = "hello world"
LONG = "line 1\nline 2\n" + "\n".join(f"line {i}" for i in range(3, 50)) + "\nThe end.\n"


def test_short_content_unchanged() -> None:
    msgs = _tool_result_pair("c1", SHORT)
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    assert report.affected == 0
    assert result[1].content[0].content == SHORT


def test_long_content_compacted() -> None:
    msgs = _tool_result_pair("c1", LONG)
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    assert report.affected == 1
    compacted = result[1].content[0].content
    assert "[compacted:" in compacted
    assert "--- head" in compacted
    assert "--- tail" in compacted
    assert "line 1" in compacted  # head preserved
    assert "The end." in compacted  # tail preserved


def test_meta_pointer_set() -> None:
    msgs = _tool_result_pair("c1", LONG)
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    meta = result[1].content[0].meta.get("compacted")
    assert meta is not None
    assert meta["original_chars"] == len(LONG)
    assert meta["tool_use_id"] == "c1"


def test_stale_blocks_skipped() -> None:
    msgs = _tool_result_pair("c1", LONG)
    msgs[1].content[0].meta["stale"] = True
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    assert report.affected == 0
    assert result[1].content[0].content == LONG


def test_keep_recent_immune() -> None:
    msgs = _tool_result_pair("c1", LONG) + _tool_result_pair("c2", LONG)
    result, report = microcompact(msgs, max_chars=100, keep_recent=1)
    assert report.affected == 1
    # First pair (c1) should be compacted, second (c2) protected
    assert "[compacted:" in result[1].content[0].content
    assert "[compacted:" not in result[3].content[0].content


def test_mixed_content_types() -> None:
    blocks: list[Block] = [
        ToolResultBlock(tool_use_id="c1", content=LONG),
        TextBlock(text="Some extra text"),
    ]
    msgs = [
        Message(role="assistant", content=[ToolUseBlock(id="c1", name="bash", input={"cmd": "x"})]),
        Message(role="user", content=blocks),
    ]
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    assert report.affected == 1
    # TextBlock should be untouched
    assert result[1].content[1].text == "Some extra text"


def test_tokens_decreased() -> None:
    msgs = _tool_result_pair("c1", LONG)
    before = count_tokens(msgs, skip_thinking=True)
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    after = count_tokens(result, skip_thinking=True)
    assert after < before


def test_error_result_skipped() -> None:
    msgs = [
        Message(role="assistant", content=[ToolUseBlock(id="c1", name="bash", input={"cmd": "x"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c1", content=LONG, is_error=True)]),
    ]
    result, report = microcompact(msgs, max_chars=100, keep_recent=0)
    assert report.affected == 0

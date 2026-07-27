from __future__ import annotations

from src.agent.context_compress import stale_snip
from src.agent.context_tokens import count_tokens
from src.agent.ir import (
    Block,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _read_pair(tool_id: str, path: str, content: str, is_error: bool = False) -> list[Message]:
    return [
        Message(role="assistant", content=[ToolUseBlock(id=tool_id, name="read", input={"path": path})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id=tool_id, content=content, is_error=is_error)]),
    ]


def _glob_pair(tool_id: str, path: str, content: str) -> list[Message]:
    return [
        Message(role="assistant", content=[ToolUseBlock(id=tool_id, name="glob", input={"pattern": path})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id=tool_id, content=content)]),
    ]


def test_single_read_no_snip() -> None:
    msgs = _read_pair("call_1", "a.py", "content a")
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 0
    assert result == msgs


def test_same_path_twice() -> None:
    msgs = _read_pair("call_1", "a.py", "old content") + _read_pair("call_2", "a.py", "new content")
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 1
    assert result[1].content[0].meta.get("stale") is True
    assert "stale" in result[1].content[0].content
    assert result[3].content[0].meta.get("stale") is None


def test_triple_read_middle_stale() -> None:
    msgs = (
        _read_pair("c1", "a.py", "v1")
        + _read_pair("c2", "a.py", "v2")
        + _read_pair("c3", "a.py", "v3")
    )
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 2
    assert "stale" in result[1].content[0].content
    assert "stale" in result[3].content[0].content
    assert "stale" not in result[5].content[0].content


def test_keep_recent_exempt() -> None:
    msgs = _read_pair("c1", "a.py", "old") + _read_pair("c2", "a.py", "new")
    result, report = stale_snip(msgs, keep_recent=2)  # protect all pairs
    assert report.affected == 0
    assert "stale" not in result[1].content[0].content


def test_error_result_skipped() -> None:
    msgs = (
        _read_pair("c1", "a.py", "content", is_error=True)
        + _read_pair("c2", "a.py", "fixed content")
    )
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 0
    assert result[1].content[0].is_error


def test_different_paths_not_affected() -> None:
    msgs = _read_pair("c1", "a.py", "a content") + _read_pair("c2", "b.py", "b content")
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 0


def test_mixed_read_and_other_tools() -> None:
    msgs = _read_pair("c1", "a.py", "old")
    msgs += [
        Message(role="assistant", content=[ToolUseBlock(id="c2", name="edit", input={"path": "a.py"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c2", content="edited")]),
    ]
    msgs += _read_pair("c3", "a.py", "new")
    result, report = stale_snip(msgs, keep_recent=0)
    # edit should not be treated as a read that makes stale_snip skip
    assert report.affected == 1
    assert "stale" in result[1].content[0].content


def test_glob_path_tracked() -> None:
    msgs = _glob_pair("c1", "*.py", "file1.py, file2.py") + _read_pair("c2", "file1.py", "content")
    result, report = stale_snip(msgs, keep_recent=0)
    # Different tools with different paths should not trigger
    assert report.affected == 0


def test_glob_pattern_snipped() -> None:
    msgs = _glob_pair("c1", "*.py", "old list") + _glob_pair("c2", "*.py", "new list")
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 1
    assert "stale" in result[1].content[0].content
    assert "stale" not in result[3].content[0].content


def test_multiple_tools_in_one_message() -> None:
    asst_blocks: list[Block] = [
        ToolUseBlock(id="c1", name="read", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="read", input={"path": "b.py"}),
    ]
    user_blocks: list[Block] = [
        ToolResultBlock(tool_use_id="c1", content="content a"),
        ToolResultBlock(tool_use_id="c2", content="content b"),
    ]
    msgs = [
        Message(role="assistant", content=asst_blocks),
        Message(role="user", content=user_blocks),
        *([Message(role="assistant", content=[ToolUseBlock(id="c3", name="read", input={"path": "a.py"})]),
           Message(role="user", content=[ToolResultBlock(tool_use_id="c3", content="new a")])]),
    ]
    result, report = stale_snip(msgs, keep_recent=0)
    assert report.affected == 1
    assert "stale" in result[1].content[0].content
    assert "stale" not in result[1].content[1].content  # b.py still current


def test_interleaved_non_read_tool() -> None:
    msgs = (
        _read_pair("c1", "a.py", "old")
        + [
            Message(role="assistant", content=[ToolUseBlock(id="c2", name="edit", input={"path": "a.py"})]),
            Message(role="user", content=[ToolResultBlock(tool_use_id="c2", content="edited a.py")]),
        ]
        + _read_pair("c3", "a.py", "new")
    )
    result, report = stale_snip(msgs, keep_recent=0)
    # 'edit' tool does NOT make the read stale — read is still stale from c1→c3
    assert report.affected == 1
    assert "stale" in result[1].content[0].content


def test_tokens_decreased() -> None:
    msgs = _read_pair("c1", "a.py", "A" * 500) + _read_pair("c2", "a.py", "B" * 500)
    before = count_tokens(msgs, skip_thinking=True)
    result, report = stale_snip(msgs, keep_recent=0)
    after = count_tokens(result, skip_thinking=True)
    assert after < before

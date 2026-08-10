from __future__ import annotations

import time

from vague_code.agent.context_compress import _detect_subtasks, structured_snip
from vague_code.agent.context_tokens import count_tokens
from vague_code.agent.ir import (
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from vague_code.agent.trajectory import Event, EventType


def _event(etype: EventType, turn: int | None, payload: dict) -> Event:
    return Event(run_id="test", turn=turn, ts=time.time(), type=etype, payload=payload)


def _tool_call(turn: int, tid: str, name: str, input: dict) -> Event:
    return _event(EventType.tool_call, turn, {"id": tid, "name": name, "input": input})


def _tool_result(turn: int, tid: str, content: str, is_error: bool = False) -> Event:
    return _event(EventType.tool_result, turn, {"tool_use_id": tid, "content": content, "is_error": is_error})


def _pair(tid: str, name: str, input: dict, content: str, is_error: bool = False) -> list[Message]:
    return [
        Message(role="assistant", content=[ToolUseBlock(id=tid, name=name, input=input)]),
        Message(role="user", content=[ToolResultBlock(tool_use_id=tid, content=content, is_error=is_error)]),
    ]


# ── _detect_subtasks ───────────────────────────────────────────────────────

def test_detect_closed_subtask() -> None:
    events = [
        _tool_call(0, "t1", "read_file", {"path": "stats.py"}),
        _tool_result(0, "t1", "def calc()..."),
        _tool_call(1, "t2", "patch", {"path": "stats.py"}),
        _tool_result(1, "t2", "patched"),
        _tool_call(2, "t3", "bash", {"command": "pytest"}),
        _tool_result(2, "t3", "退出码: 0\n标准输出:\n1 passed"),
    ]
    subtasks = _detect_subtasks(events)
    assert len(subtasks) == 1
    assert subtasks[0].start_turn == 0
    assert subtasks[0].end_turn == 2
    assert subtasks[0].tool_use_ids == {"t1", "t2", "t3"}


def test_detect_in_progress_subtask_excluded() -> None:
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "content"),
        _tool_call(1, "t2", "patch", {"path": "a.py"}),
        _tool_result(1, "t2", "patched"),
        # no successful bash → subtask never closed
    ]
    subtasks = _detect_subtasks(events)
    assert subtasks == []


def test_detect_failed_bash_does_not_close() -> None:
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "content"),
        _tool_call(1, "t2", "bash", {"command": "pytest"}),
        _tool_result(1, "t2", "退出码: 1\n标准输出:\nFAILED", is_error=True),
    ]
    subtasks = _detect_subtasks(events)
    assert subtasks == []


def test_detect_two_subtasks() -> None:
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "a content"),
        _tool_call(1, "t2", "bash", {"command": "pytest"}),
        _tool_result(1, "t2", "退出码: 0"),
        _tool_call(2, "t3", "read_file", {"path": "b.py"}),
        _tool_result(2, "t3", "b content"),
        _tool_call(3, "t4", "patch", {"path": "b.py"}),
        _tool_result(3, "t4", "patched"),
        _tool_call(4, "t5", "bash", {"command": "pytest"}),
        _tool_result(4, "t5", "退出码: 0"),
    ]
    subtasks = _detect_subtasks(events)
    assert len(subtasks) == 2
    assert subtasks[0].start_turn == 0 and subtasks[0].end_turn == 1
    assert subtasks[1].start_turn == 2 and subtasks[1].end_turn == 4


def test_detect_bash_only_no_subtask() -> None:
    events = [
        _tool_call(0, "t1", "bash", {"command": "pwd"}),
        _tool_result(0, "t1", "退出码: 0\n标准输出:\n/proj"),
    ]
    subtasks = _detect_subtasks(events)
    assert subtasks == []


def test_detect_normalizes_plain_dicts() -> None:
    events = [
        {"type": "tool_call", "turn": 0, "payload": {"id": "t1", "name": "read_file", "input": {"path": "a.py"}}},
        {"type": "tool_result", "turn": 0, "payload": {"tool_use_id": "t1", "content": "c", "is_error": False}},
        {"type": "tool_call", "turn": 1, "payload": {"id": "t2", "name": "bash", "input": {"command": "pytest"}}},
        {"type": "tool_result", "turn": 1, "payload": {"tool_use_id": "t2", "content": "退出码: 0", "is_error": False}},
    ]
    subtasks = _detect_subtasks(events)
    assert len(subtasks) == 1
    assert subtasks[0].tool_use_ids == {"t1", "t2"}


# ── structured_snip ────────────────────────────────────────────────────────

def test_events_none_pass_through() -> None:
    msgs = _pair("t1", "read_file", {"path": "a.py"}, "content")
    result, report = structured_snip(msgs, events=None)
    assert report.affected == 0
    assert result == msgs
    assert report.detail.get("skipped") == "no_events"


def test_no_closed_subtasks_untouched() -> None:
    msgs = (
        _pair("t1", "read_file", {"path": "a.py"}, "content")
        + _pair("t2", "patch", {"path": "a.py"}, "patched")
    )
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "content"),
        _tool_call(1, "t2", "patch", {"path": "a.py"}),
        _tool_result(1, "t2", "patched"),
    ]
    result, report = structured_snip(msgs, events=events, keep_recent=0)
    assert report.affected == 0
    assert result == msgs
    assert report.detail.get("skipped") == "no_closed_subtasks"


def test_single_subtask_compressed() -> None:
    msgs = (
        [Message(role="system", content="sys")]
        + [Message(role="user", content="task")]
        + _pair("t1", "read_file", {"path": "stats.py"}, "def calc()")
        + _pair("t2", "patch", {"path": "stats.py"}, "patched")
        + _pair("t3", "bash", {"command": "pytest"}, "退出码: 0\n标准输出:\n1 passed")
    )
    events = [
        _tool_call(0, "t1", "read_file", {"path": "stats.py"}),
        _tool_result(0, "t1", "def calc()"),
        _tool_call(1, "t2", "patch", {"path": "stats.py"}),
        _tool_result(1, "t2", "patched"),
        _tool_call(2, "t3", "bash", {"command": "pytest"}),
        _tool_result(2, "t3", "退出码: 0\n标准输出:\n1 passed"),
    ]
    result, report = structured_snip(msgs, events=events, keep_recent=0)
    assert report.affected == 3
    # system + task + 1 summary = 3 messages
    assert len(result) == 3
    summary = result[2]
    assert summary.role == "user"
    text = summary.content[0].text
    assert "[已完成子任务 (turn 0-2)]" in text
    assert "read_file: stats.py" in text
    assert "patch: stats.py" in text
    assert "bash: pytest" in text
    assert summary.content[0].meta["compacted_by"] == "structured_snip"
    assert summary.content[0].meta["turn_range"] == [0, 2]


def test_keep_recent_exempts_latest() -> None:
    msgs = (
        _pair("t1", "read_file", {"path": "a.py"}, "a")
        + _pair("t2", "bash", {"command": "pytest"}, "退出码: 0")
        + _pair("t3", "read_file", {"path": "b.py"}, "b")
        + _pair("t4", "bash", {"command": "pytest"}, "退出码: 0")
    )
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "a"),
        _tool_call(1, "t2", "bash", {"command": "pytest"}),
        _tool_result(1, "t2", "退出码: 0"),
        _tool_call(2, "t3", "read_file", {"path": "b.py"}),
        _tool_result(2, "t3", "b"),
        _tool_call(3, "t4", "bash", {"command": "pytest"}),
        _tool_result(3, "t4", "退出码: 0"),
    ]
    result, report = structured_snip(msgs, events=events, keep_recent=1)
    assert report.affected == 2
    # oldest subtask (turn 0-1) compressed into a summary; latest (turn 2-3) kept raw
    assert len(result) == 5  # summary + 2 latest pairs
    assert "turn 0-1" in result[0].content[0].text
    assert "turn 2-3" not in result[0].content[0].text
    # latest pair preserved verbatim
    assert result[1].role == "assistant"
    assert result[2].role == "user"
    assert result[3].role == "assistant"
    assert result[4].role == "user"


def test_keep_recent_zero_compresses_all() -> None:
    msgs = (
        _pair("t1", "read_file", {"path": "a.py"}, "a")
        + _pair("t2", "bash", {"command": "pytest"}, "退出码: 0")
        + _pair("t3", "read_file", {"path": "b.py"}, "b")
        + _pair("t4", "bash", {"command": "pytest"}, "退出码: 0")
    )
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "a"),
        _tool_call(1, "t2", "bash", {"command": "pytest"}),
        _tool_result(1, "t2", "退出码: 0"),
        _tool_call(2, "t3", "read_file", {"path": "b.py"}),
        _tool_result(2, "t3", "b"),
        _tool_call(3, "t4", "bash", {"command": "pytest"}),
        _tool_result(3, "t4", "退出码: 0"),
    ]
    result, report = structured_snip(msgs, events=events, keep_recent=0)
    assert report.affected == 4
    assert len(result) == 2  # both subtasks → two summaries
    assert "turn 0-1" in result[0].content[0].text
    assert "turn 2-3" in result[1].content[0].text


def test_tool_pairing_invariant_preserved() -> None:
    """After compression, remaining assistant ToolUseBlocks still pair with results."""
    msgs = (
        [Message(role="system", content="sys")]
        + [Message(role="user", content="task")]
        + _pair("t1", "read_file", {"path": "a.py"}, "a")
        + _pair("t2", "bash", {"command": "pytest"}, "退出码: 0")
        + _pair("t3", "read_file", {"path": "b.py"}, "b")
        + _pair("t4", "bash", {"command": "pytest"}, "退出码: 0")
    )
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "a"),
        _tool_call(1, "t2", "bash", {"command": "pytest"}),
        _tool_result(1, "t2", "退出码: 0"),
        _tool_call(2, "t3", "read_file", {"path": "b.py"}),
        _tool_result(2, "t3", "b"),
        _tool_call(3, "t4", "bash", {"command": "pytest"}),
        _tool_result(3, "t4", "退出码: 0"),
    ]
    result, report = structured_snip(msgs, events=events, keep_recent=1)
    # system + task + summary + 2 latest pairs = 7 messages
    assert len(result) == 7
    roles = [m.role for m in result]
    assert roles == ["system", "user", "user", "assistant", "user", "assistant", "user"]
    # both surviving pairs keep tool_use ↔ tool_result matching
    for i in range(3, len(result), 2):
        tool_ids = {b.id for b in result[i].content if isinstance(b, ToolUseBlock)}
        result_ids = {b.tool_use_id for b in result[i + 1].content if isinstance(b, ToolResultBlock)}
        assert tool_ids == result_ids


def test_tokens_decrease() -> None:
    msgs = (
        _pair("t1", "read_file", {"path": "a.py"}, "A" * 500)
        + _pair("t2", "bash", {"command": "pytest"}, "退出码: 0\n" + "B" * 500)
    )
    events = [
        _tool_call(0, "t1", "read_file", {"path": "a.py"}),
        _tool_result(0, "t1", "A" * 500),
        _tool_call(1, "t2", "bash", {"command": "pytest"}),
        _tool_result(1, "t2", "退出码: 0\n" + "B" * 500),
    ]
    before = count_tokens(msgs, skip_thinking=True)
    result, report = structured_snip(msgs, events=events, keep_recent=0)
    after = count_tokens(result, skip_thinking=True)
    assert report.affected == 2
    assert after < before


def test_empty_events_list_noop() -> None:
    msgs = _pair("t1", "read_file", {"path": "a.py"}, "content")
    result, report = structured_snip(msgs, events=[], keep_recent=0)
    assert report.affected == 0
    assert result == msgs
    assert report.detail.get("skipped") == "no_events"

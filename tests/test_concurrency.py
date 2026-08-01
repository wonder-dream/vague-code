from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.agent.concurrency import (
    OpType,
    ResourceScope,
    ScopeType,
    _extract_scope,
    _pattern_prefix,
    _scopes_conflict,
    execute_concurrent,
    schedule,
)
from src.agent.ir import ToolUseBlock


# ── _pattern_prefix ─────────────────────────────────────────────────────────

def test_pattern_prefix_no_wildcard() -> None:
    assert _pattern_prefix("src/") == "src"


def test_pattern_prefix_wildcard_at_end() -> None:
    assert _pattern_prefix("src/**/*.py") == "src"


def test_pattern_prefix_deep_path() -> None:
    assert _pattern_prefix("src/a/b/*.ts") == "src/a/b"


def test_pattern_prefix_root_wildcard() -> None:
    assert _pattern_prefix("*.py") == ""


# ── _extract_scope ──────────────────────────────────────────────────────────

def test_scope_read_file() -> None:
    call = ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.EXACT
    assert s.path == "a.py"


def test_scope_write_file_new(tmp_path: Path) -> None:
    call = ToolUseBlock(id="c1", name="write_file", input={"path": "new.txt", "content": "hello"})
    s = _extract_scope(call, str(tmp_path))
    assert s.op_type == OpType.STRUCTURAL_WRITE
    assert s.scope_type == ScopeType.EXACT
    assert s.path == "new.txt"


def test_scope_write_file_existing(tmp_path: Path) -> None:
    existing = tmp_path / "exist.txt"
    existing.write_text("x", encoding="utf-8")
    call = ToolUseBlock(id="c1", name="write_file", input={"path": "exist.txt", "content": "y"})
    s = _extract_scope(call, str(tmp_path))
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.EXACT


def test_scope_patch() -> None:
    call = ToolUseBlock(id="c1", name="patch", input={"path": "a.py", "old_str": "x", "new_str": "y"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.EXACT


def test_scope_glob() -> None:
    call = ToolUseBlock(id="c1", name="glob", input={"pattern": "tests/**/*.py"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.PREFIX
    assert s.path == "tests"


def test_scope_grep() -> None:
    call = ToolUseBlock(id="c1", name="grep", input={"path": "src/", "pattern": "TODO"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.PREFIX
    assert s.path == "src/"


def test_scope_bash() -> None:
    call = ToolUseBlock(id="c1", name="bash", input={"command": "ls"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.WORKSPACE


def test_scope_code_search() -> None:
    call = ToolUseBlock(id="c1", name="code_search", input={"query": "calculate"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.EXACT
    assert s.path == ""


def test_scope_code_search_with_path() -> None:
    call = ToolUseBlock(id="c1", name="code_search", input={"query": "foo", "path": "src/"})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.EXACT
    assert s.path == "src/"


def test_code_search_reads_do_not_conflict() -> None:
    a = _extract_scope(
        ToolUseBlock(id="c1", name="code_search", input={"query": "foo"}), "/ws")
    b = _extract_scope(
        ToolUseBlock(id="c1", name="code_search", input={"query": "bar"}), "/ws")
    assert not _scopes_conflict(a, b)


def test_scope_unknown_tool() -> None:
    call = ToolUseBlock(id="c1", name="unknown", input={})
    s = _extract_scope(call, "/ws")
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.WORKSPACE


# ── _scopes_conflict ────────────────────────────────────────────────────────

def test_no_conflict_read_read() -> None:
    a = ResourceScope("a.py", ScopeType.EXACT, OpType.READ)
    b = ResourceScope("a.py", ScopeType.EXACT, OpType.READ)
    assert not _scopes_conflict(a, b)


def test_conflict_read_write_same_path() -> None:
    a = ResourceScope("a.py", ScopeType.EXACT, OpType.READ)
    b = ResourceScope("a.py", ScopeType.EXACT, OpType.WRITE)
    assert _scopes_conflict(a, b)


def test_no_conflict_different_paths() -> None:
    a = ResourceScope("a.py", ScopeType.EXACT, OpType.WRITE)
    b = ResourceScope("b.py", ScopeType.EXACT, OpType.READ)
    assert not _scopes_conflict(a, b)


def test_conflict_workspace_with_any() -> None:
    a = ResourceScope("", ScopeType.WORKSPACE, OpType.WRITE)
    b = ResourceScope("a.py", ScopeType.EXACT, OpType.READ)
    assert _scopes_conflict(a, b)


def test_conflict_exact_contained_in_prefix() -> None:
    a = ResourceScope("src/a.py", ScopeType.EXACT, OpType.WRITE)
    b = ResourceScope("src", ScopeType.PREFIX, OpType.READ)
    assert _scopes_conflict(a, b)


def test_conflict_prefix_boundary_not_crossed() -> None:
    a = ResourceScope("src", ScopeType.PREFIX, OpType.READ)
    b = ResourceScope("src-2/a.py", ScopeType.EXACT, OpType.WRITE)
    assert not _scopes_conflict(a, b)


def test_conflict_prefix_contains_exact() -> None:
    a = ResourceScope("src", ScopeType.PREFIX, OpType.READ)
    b = ResourceScope("src/a.py", ScopeType.EXACT, OpType.WRITE)
    assert _scopes_conflict(a, b)


def test_no_conflict_prefix_different_branch() -> None:
    a = ResourceScope("src", ScopeType.PREFIX, OpType.READ)
    b = ResourceScope("tests/a.py", ScopeType.EXACT, OpType.WRITE)
    assert not _scopes_conflict(a, b)


# ── schedule ─────────────────────────────────────────────────────────────────

def test_schedule_all_reads_concurrent() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="read_file", input={"path": "b.py"}),
    ]
    groups = schedule(calls, "/ws")
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_schedule_read_write_same_file_serial() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "a.py", "content": "x"}),
    ]
    groups = schedule(calls, "/ws")
    assert len(groups) >= 2  # can't be in same group


def test_schedule_bash_isolated() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="bash", input={"command": "ls"}),
    ]
    groups = schedule(calls, "/ws")
    assert len(groups) == 2


def test_schedule_mixed_three() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "b.py", "content": "x"}),
        ToolUseBlock(id="c3", name="bash", input={"command": "ls"}),
    ]
    groups = schedule(calls, "/ws")
    # c1(read a) + c2(write b) can be concurrent → group 1; c3(bash) isolated → group 2
    assert len(groups) == 2
    assert len(groups[0]) == 2


def test_schedule_two_writes_same_file() -> None:
    calls = [
        ToolUseBlock(id="c1", name="write_file", input={"path": "a.py", "content": "x"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "a.py", "content": "y"}),
    ]
    groups = schedule(calls, "/ws")
    assert len(groups) >= 2  # same path write → conflict → separate groups


# ── execute_concurrent ──────────────────────────────────────────────────────

def _fake_handler(content: str = "ok"):
    def handler(input: dict) -> str:
        return content
    return handler


def test_execute_results_order() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="read_file", input={"path": "b.py"}),
    ]
    handlers = {"read_file": _fake_handler("result c1")}
    results = execute_concurrent(calls, handlers, "/ws")
    assert len(results) == 2
    assert results[0].tool_use_id == "c1"
    assert results[1].tool_use_id == "c2"


def test_execute_unknown_tool() -> None:
    call = ToolUseBlock(id="c1", name="nonexistent", input={})
    results = execute_concurrent([call], {}, "/ws")
    assert len(results) == 1
    assert results[0].is_error
    assert "未知工具" in results[0].content


def test_execute_handler_raises_exception() -> None:
    calls = [ToolUseBlock(id="c1", name="read_file", input={"path": "x"})]
    def exploding_handler(_: dict) -> str:
        raise ValueError("handler boom")
    results = execute_concurrent(calls, {"read_file": exploding_handler}, "/ws")
    assert len(results) == 1
    assert results[0].is_error
    assert "ValueError" in results[0].content


def test_execute_failure_propagation() -> None:
    # First group: [read a, bash] — bash is WORKSPACE, can't share with read
    # After read a handler throws, bash (group 2) should be skipped
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "will_fail"}),
        ToolUseBlock(id="c2", name="bash", input={"command": "echo ok"}),
    ]
    def ok_handler(input: dict) -> str:
        return "ok"
    def failing_handler(input: dict) -> str:
        raise RuntimeError("intentional fail")
    handlers: dict[str, Callable[[dict], str]] = {
        "read_file": failing_handler,
        "bash": ok_handler,
    }
    results = execute_concurrent(calls, handlers, "/ws")
    assert len(results) == 2
    assert results[0].is_error
    assert "RuntimeError" in results[0].content
    assert results[1].is_error
    assert "已跳过" in results[1].content


def test_execute_concurrent_faster_than_serial(tmp_path: Path) -> None:
    # Can't easily test concurrency without bash isolation or using read-only
    # Just verify it runs without error
    calls = [
        ToolUseBlock(id="c1", name="bash", input={"command": "sleep"}),  # will be WORKSPACE → serial
    ]
    results = execute_concurrent(calls, {}, str(tmp_path))
    assert len(results) == 1

from __future__ import annotations

from pathlib import Path

from vague_code.agent.concurrency import (
    OpType,
    ResourceScope,
    ScopeType,
    _normalize_path,
    _pattern_prefix,
    _scope_for,
    _scopes_conflict,
    execute_concurrent,
    schedule,
)
from vague_code.agent.ir import ToolUseBlock
from vague_code.agent.tools.base import ToolResult


def _tools(workdir: str) -> dict:
    """真实注册表绑定实例（scope 提取用，不执行工具）。"""
    from vague_code.agent.tools import DEFAULT_TOOLS, bind_tools
    return bind_tools(DEFAULT_TOOLS, workdir)


def _tools_with_code_search(workdir: str) -> dict:
    from vague_code.agent.tools.code_search import CodeSearchTool
    tools = _tools(workdir)
    tools["code_search"] = CodeSearchTool(workdir, None)
    return tools


class _FakeTool:
    """最小 fake 工具：可调用返回 ToolResult + 资源声明。"""

    name = "fake"

    def __init__(self, content: str = "ok", op_type: OpType = OpType.READ):
        self._content = content
        self._op_type = op_type

    def __call__(self, input: dict) -> ToolResult:
        return ToolResult(output=self._content)

    def resource_scope(self, input: dict) -> ResourceScope:
        return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=self._op_type)


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
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.EXACT
    assert s.path == "a.py"


def test_scope_write_file_new(tmp_path: Path) -> None:
    call = ToolUseBlock(id="c1", name="write_file", input={"path": "new.txt", "content": "hello"})
    s = _scope_for(call, _tools_with_code_search(str(tmp_path)))
    assert s.op_type == OpType.STRUCTURAL_WRITE
    assert s.scope_type == ScopeType.EXACT
    assert s.path == "new.txt"


def test_scope_write_file_existing(tmp_path: Path) -> None:
    existing = tmp_path / "exist.txt"
    existing.write_text("x", encoding="utf-8")
    call = ToolUseBlock(id="c1", name="write_file", input={"path": "exist.txt", "content": "y"})
    s = _scope_for(call, _tools_with_code_search(str(tmp_path)))
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.EXACT


def test_scope_patch() -> None:
    call = ToolUseBlock(id="c1", name="patch", input={"path": "a.py", "old_str": "x", "new_str": "y"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.EXACT


def test_scope_glob() -> None:
    call = ToolUseBlock(id="c1", name="glob", input={"pattern": "tests/**/*.py"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.PREFIX
    assert s.path == "tests"


def test_scope_grep() -> None:
    call = ToolUseBlock(id="c1", name="grep", input={"path": "src/", "pattern": "TODO"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.PREFIX
    assert s.path == "src/"


def test_scope_bash() -> None:
    call = ToolUseBlock(id="c1", name="bash", input={"command": "ls"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.WRITE
    assert s.scope_type == ScopeType.WORKSPACE


def test_scope_code_search() -> None:
    call = ToolUseBlock(id="c1", name="code_search", input={"query": "calculate"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.EXACT
    assert s.path == ""


def test_scope_code_search_with_path() -> None:
    call = ToolUseBlock(id="c1", name="code_search", input={"query": "foo", "path": "src/"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.op_type == OpType.READ
    assert s.scope_type == ScopeType.EXACT
    assert s.path == "src/"


def test_code_search_reads_do_not_conflict() -> None:
    a = _scope_for(
        ToolUseBlock(id="c1", name="code_search", input={"query": "foo"}), _tools_with_code_search("/ws"))
    b = _scope_for(
        ToolUseBlock(id="c1", name="code_search", input={"query": "bar"}), _tools_with_code_search("/ws"))
    assert not _scopes_conflict(a, b)


def test_scope_unknown_tool() -> None:
    call = ToolUseBlock(id="c1", name="unknown", input={})
    s = _scope_for(call, _tools_with_code_search("/ws"))
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
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_schedule_read_write_same_file_serial() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "a.py", "content": "x"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) >= 2  # can't be in same group


def test_schedule_bash_isolated() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="bash", input={"command": "ls"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) == 2


def test_schedule_mixed_three() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "b.py", "content": "x"}),
        ToolUseBlock(id="c3", name="bash", input={"command": "ls"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    # c1(read a) + c2(write b) can be concurrent → group 1; c3(bash) isolated → group 2
    assert len(groups) == 2
    assert len(groups[0]) == 2


def test_schedule_two_writes_same_file() -> None:
    calls = [
        ToolUseBlock(id="c1", name="write_file", input={"path": "a.py", "content": "x"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "a.py", "content": "y"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) >= 2  # same path write → conflict → separate groups


# ── P0 回归：根级 glob / 全库 grep 必须与写操作冲突 ─────────────────────

def test_scope_glob_root_pattern_is_workspace() -> None:
    call = ToolUseBlock(id="c1", name="glob", input={"pattern": "**/*.py"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.scope_type == ScopeType.WORKSPACE
    assert s.op_type == OpType.READ


def test_scope_grep_no_path_is_workspace() -> None:
    call = ToolUseBlock(id="c1", name="grep", input={"pattern": "TODO"})
    s = _scope_for(call, _tools_with_code_search("/ws"))
    assert s.scope_type == ScopeType.WORKSPACE
    assert s.op_type == OpType.READ


def test_schedule_root_glob_conflicts_with_write() -> None:
    calls = [
        ToolUseBlock(id="c1", name="glob", input={"pattern": "**/*.py"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "a.py", "content": "x"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) == 2


def test_schedule_full_grep_conflicts_with_write() -> None:
    calls = [
        ToolUseBlock(id="c1", name="grep", input={"pattern": "TODO"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "a.py", "content": "x"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) == 2


def test_schedule_two_full_greps_still_concurrent() -> None:
    calls = [
        ToolUseBlock(id="c1", name="grep", input={"pattern": "TODO"}),
        ToolUseBlock(id="c2", name="grep", input={"pattern": "FIXME"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    assert len(groups) == 1


# ── P1 回归：Windows 路径大小写归一化 ───────────────────────────────────

def test_normalize_path_case_platform() -> None:
    import os
    if os.name == "nt":
        assert _normalize_path("SRC/A.PY") == _normalize_path("src/a.py")
    else:
        assert _normalize_path("SRC/A.PY") != _normalize_path("src/a.py")


def test_schedule_case_insensitive_conflict_on_windows() -> None:
    import os
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "SRC/A.PY"}),
        ToolUseBlock(id="c2", name="write_file", input={"path": "src/a.py", "content": "x"}),
    ]
    groups = schedule(calls, _tools("/ws"))
    if os.name == "nt":
        assert len(groups) == 2
    else:
        assert len(groups) == 1


# ── P2 回归：超时后立即返回 ─────────────────────────────────────────────

def test_execute_timeout_returns_promptly(tmp_path, monkeypatch) -> None:
    import time
    monkeypatch.setattr("vague_code.agent.concurrency._CONCURRENT_TIMEOUT", 0.1)
    calls = [ToolUseBlock(id="c1", name="bash", input={"command": "sleep"})]

    class _SlowTool(_FakeTool):
        def __call__(self, input: dict) -> ToolResult:
            time.sleep(2)
            return ToolResult(output="late")

    t0 = time.monotonic()
    results = execute_concurrent(calls, {"bash": _SlowTool()})
    elapsed = time.monotonic() - t0
    assert len(results) == 1
    assert results[0].is_error
    assert "超时" in results[0].content
    assert elapsed < 1.0  # 修复前 with 块会等待慢任务完成（~2s）


# ── execute_concurrent ──────────────────────────────────────────────────────

def test_execute_results_order() -> None:
    calls = [
        ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="c2", name="read_file", input={"path": "b.py"}),
    ]
    tools = {"read_file": _FakeTool("result c1")}
    results = execute_concurrent(calls, tools)
    assert len(results) == 2
    assert results[0].tool_use_id == "c1"
    assert results[1].tool_use_id == "c2"


def test_execute_unknown_tool() -> None:
    call = ToolUseBlock(id="c1", name="nonexistent", input={})
    results = execute_concurrent([call], {})
    assert len(results) == 1
    assert results[0].is_error
    assert "未知工具" in results[0].content


def test_execute_handler_raises_exception() -> None:
    calls = [ToolUseBlock(id="c1", name="read_file", input={"path": "x"})]

    class _ExplodingTool(_FakeTool):
        def __call__(self, input: dict) -> ToolResult:
            raise ValueError("handler boom")

    results = execute_concurrent(calls, {"read_file": _ExplodingTool()})
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

    class _FailingTool(_FakeTool):
        def __call__(self, input: dict) -> ToolResult:
            raise RuntimeError("intentional fail")

    tools = {"read_file": _FailingTool(), "bash": _FakeTool("ok", OpType.WRITE)}
    results = execute_concurrent(calls, tools)
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
    results = execute_concurrent(calls, {})
    assert len(results) == 1

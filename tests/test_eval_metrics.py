from __future__ import annotations

import subprocess
from pathlib import Path

from src.agent.trajectory import Event, EventType

from eval.metrics import (
    diff_touches_test_files,
    metrics_from_events,
    trajectory_grade,
)


def _ev(etype: EventType, turn: int | None, payload: dict) -> Event:
    return Event(run_id="r1", turn=turn, ts=0.0, type=etype, payload=payload)


def _tool(name: str, input: dict, turn: int) -> Event:
    return _ev(EventType.tool_call, turn, {"id": "x", "name": name, "input": input})


# ── read-before-edit / 冗余 / 验证循环 ──────────────────────────────────

def test_read_before_edit_rate() -> None:
    evs = [
        _tool("read_file", {"path": "a.py"}, 1),
        _tool("write_file", {"path": "a.py", "content": "x"}, 3),   # 有读
        _tool("write_file", {"path": "b.py", "content": "y"}, 4),   # 无读
    ]
    m = metrics_from_events(evs)
    assert m.total_edits == 2
    assert m.edits_with_read == 1
    assert m.read_before_edit_rate == 0.5


def test_read_before_edit_respects_window() -> None:
    evs = [
        _tool("read_file", {"path": "a.py"}, 1),
        _tool("write_file", {"path": "a.py", "content": "x"}, 10),  # 超出 5 轮窗口
    ]
    m = metrics_from_events(evs)
    assert m.read_before_edit_rate == 0.0


def test_redundant_reads_and_greps() -> None:
    evs = [
        _tool("read_file", {"path": "a.py"}, 1),
        _tool("read_file", {"path": "a.py"}, 2),          # 冗余
        _tool("grep", {"pattern": "TODO", "path": "src"}, 1),
        _tool("grep", {"pattern": "TODO", "path": "src"}, 2),  # 冗余
    ]
    m = metrics_from_events(evs)
    assert m.redundant_reads == 1
    assert m.redundant_greps == 1


def test_edit_then_test() -> None:
    evs = [
        _tool("write_file", {"path": "a.py", "content": "x"}, 2),
        _tool("bash", {"command": "python -m pytest tests"}, 3),
    ]
    assert metrics_from_events(evs).edit_then_test is True

    evs2 = [
        _tool("write_file", {"path": "a.py", "content": "x"}, 2),
        _tool("bash", {"command": "ls -la"}, 3),
    ]
    assert metrics_from_events(evs2).edit_then_test is False


def test_error_calls_and_permission_denies() -> None:
    evs = [
        _tool("bash", {"command": "rm -rf /"}, 1),
        _ev(EventType.permission_check, 1, {"tool": "bash", "decision": "deny", "command": "rm -rf /"}),
        _ev(EventType.permission_check, 1, {"tool": "bash", "decision": "allow", "command": "ls"}),
        _ev(EventType.tool_result, 1, {"tool_use_id": "x", "content": "err", "is_error": True}),
        _ev(EventType.tool_result, 1, {"tool_use_id": "y", "content": "ok", "is_error": False}),
    ]
    m = metrics_from_events(evs)
    assert m.error_calls == 1
    assert m.permission_denies == 1
    assert m.denied_tools == ["bash"]


def test_tool_counts() -> None:
    evs = [
        _tool("read_file", {"path": "a.py"}, 1),
        _tool("read_file", {"path": "b.py"}, 2),
        _tool("bash", {"command": "ls"}, 3),
    ]
    m = metrics_from_events(evs)
    assert m.tool_total == 3
    assert m.unique_tools == 2
    assert m.tool_counts["read_file"] == 2


# ── 轨迹匹配分级 ────────────────────────────────────────────────────────

def test_trajectory_grade_exact() -> None:
    g, p, r = trajectory_grade(["read_file", "write_file"], ["read_file", "write_file"])
    assert g == "exact" and p == 1.0 and r == 1.0


def test_trajectory_grade_ordered_allows_insertions() -> None:
    gold = ["read_file", "patch", "bash"]
    actual = ["grep", "read_file", "glob", "patch", "read_file", "bash", "bash"]
    g, p, r = trajectory_grade(actual, gold)
    assert g == "ordered" and r == 1.0


def test_trajectory_grade_any_order() -> None:
    gold = ["read_file", "patch"]
    actual = ["patch", "read_file"]
    g, p, r = trajectory_grade(actual, gold)
    assert g == "any"


def test_trajectory_grade_miss() -> None:
    gold = ["read_file", "patch", "bash"]
    actual = ["glob", "grep"]
    g, p, r = trajectory_grade(actual, gold)
    assert g == "miss" and r == 0.0


# ── diff 触碰测试文件（P0-3 / P2 钻空子） ────────────────────────────────

TEST_PATCH = """diff --git a/test_app.py b/test_app.py
--- a/test_app.py
+++ b/test_app.py
@@ -1,1 +1,1 @@
-x
+y
"""


def _init_repo(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q"], ["config", "user.email", "t@x"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True)
    (d / "app.py").write_text("def a():\n    pass\n", encoding="utf-8")
    (d / "test_app.py").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(d), "add", "."], capture_output=True, text=True)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "base"],
                   capture_output=True, text=True)


def test_diff_touches_test_files(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "app.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("y\n", encoding="utf-8")
    assert diff_touches_test_files(tmp_path, TEST_PATCH) == ["test_app.py"]


def test_diff_touches_ignores_clean_test(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "app.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    assert diff_touches_test_files(tmp_path, TEST_PATCH) == []

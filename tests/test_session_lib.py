"""list_recent_runs tests: mode filter must not be crowded out by task runs (ADR-0026 fix)."""

from __future__ import annotations

import json
import sqlite3

from vague_code.agent.trajectory import SCHEMA_EVENTS, SCHEMA_INDEX_EVENTS, SCHEMA_RUNS
from vague_code.tui.session_lib import list_recent_runs


def _make_db(path, runs: list[tuple[str, str, float, str | None]]) -> None:
    """runs: (run_id, task, created_at, mode|None). mode None = no run_start event."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(SCHEMA_RUNS)
        conn.execute(SCHEMA_EVENTS)
        conn.execute(SCHEMA_INDEX_EVENTS)
        for run_id, task, ts, mode in runs:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, task, status, created_at) "
                "VALUES (?, ?, ?, ?)",
                (run_id, task, "end_turn", ts),
            )
            if mode is not None:
                conn.execute(
                    "INSERT INTO events (run_id, turn, ts, type, payload) VALUES (?, ?, ?, ?, ?)",
                    (run_id, None, ts, "run_start",
                     json.dumps({"task": task, "mode": mode}, ensure_ascii=False)),
                )
        conn.commit()
    finally:
        conn.close()


def test_mode_chat_not_crowded_out_by_task_runs(tmp_path) -> None:
    db = tmp_path / "runs.db"
    runs: list[tuple[str, str, float, str | None]] = []
    # 10 newer task-mode runs
    for i in range(10):
        runs.append((f"task{i:04x}", f"task {i}", 1000.0 - i, "task"))
    # 3 older chat runs (older timestamps: created BEFORE the task flood)
    runs.append(("chat0001", "old chat one", 500.0, "chat"))
    runs.append(("chat0002", "old chat two", 400.0, "chat"))
    runs.append(("chat0003", "old chat three", 300.0, "chat"))
    _make_db(db, runs)

    default = list_recent_runs(str(db), limit=10)
    assert len(default) == 10
    assert all(r.mode == "task" for r in default)
    assert not any(r.run_id == "chat0001" for r in default)

    chat = list_recent_runs(str(db), limit=10, mode="chat")
    assert [r.run_id for r in chat] == ["chat0001", "chat0002", "chat0003"]
    assert all(r.mode == "chat" for r in chat)


def test_mode_none_returns_mixed_latest(tmp_path) -> None:
    db = tmp_path / "runs.db"
    runs: list[tuple[str, str, float, str | None]] = [
        ("chatA", "chat A", 200.0, "chat"),
        ("taskB", "task B", 100.0, "task"),
    ]
    _make_db(db, runs)
    result = list_recent_runs(str(db), limit=10)
    assert [r.run_id for r in result] == ["chatA", "taskB"]


def test_legacy_db_without_events_table(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE runs (run_id TEXT, task TEXT, status TEXT, created_at REAL)")
        conn.execute("INSERT INTO runs VALUES ('r1', 'hello', 'end_turn', 100.0)")
        conn.commit()
    finally:
        conn.close()
    # no events table: mode filter unsupported, must not crash
    result = list_recent_runs(str(db), limit=10)
    assert len(result) == 1
    assert result[0].run_id == "r1"


def test_missing_db_returns_empty(tmp_path) -> None:
    assert list_recent_runs(str(tmp_path / "nope.db"), limit=10) == []

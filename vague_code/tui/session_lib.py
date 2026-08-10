"""Session list + memory lookups shared by commands and pickers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunInfo:
    run_id: str
    task: str
    status: str
    mode: str = "task"
    title: str = ""


def list_recent_runs(db_path: str, limit: int = 10, mode: str | None = None) -> list[RunInfo]:
    try:
        conn = sqlite3.connect(db_path)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "events" not in tables:
                rows = conn.execute(
                    "SELECT run_id, task, status FROM runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [RunInfo(str(run_id), task or "", status or "") for run_id, task, status in rows]
            if mode is None:
                rows = conn.execute(
                    """SELECT r.run_id, r.task, r.status,
                              COALESCE((SELECT json_extract(e.payload, '$.mode') FROM events e
                                        WHERE e.run_id = r.run_id AND e.type = 'run_start' LIMIT 1), 'task'),
                              COALESCE(r.title, '')
                       FROM runs r ORDER BY r.created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT r.run_id, r.task, r.status,
                              COALESCE((SELECT json_extract(e.payload, '$.mode') FROM events e
                                        WHERE e.run_id = r.run_id AND e.type = 'run_start' LIMIT 1), 'task'),
                              COALESCE(r.title, '')
                       FROM runs r
                       WHERE (SELECT json_extract(e.payload, '$.mode') FROM events e
                              WHERE e.run_id = r.run_id AND e.type = 'run_start' LIMIT 1) = ?
                       ORDER BY r.created_at DESC LIMIT ?""",
                    (mode, limit),
                ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [
        RunInfo(str(run_id), task or "", status or "", mode or "task", title or "")
        for run_id, task, status, mode, title in rows
    ]

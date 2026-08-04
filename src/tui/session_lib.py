"""Session list + memory lookups shared by commands and pickers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunInfo:
    run_id: str
    task: str
    status: str


def list_recent_runs(db_path: str, limit: int = 10) -> list[RunInfo]:
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT run_id, task, status FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [RunInfo(str(run_id), task or "", status or "") for run_id, task, status in rows]

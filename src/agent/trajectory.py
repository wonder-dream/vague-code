from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import cast

from src.agent.config import AgentConfig
from src.agent.ir import (
    Block,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class EventType(str, Enum):
    run_start = "run_start"
    turn_start = "turn_start"
    llm_response = "llm_response"
    tool_call = "tool_call"
    tool_result = "tool_result"
    error = "error"
    run_end = "run_end"


@dataclass
class Event:
    run_id: str
    turn: int | None
    ts: float
    type: EventType
    payload: dict

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "turn": self.turn,
            "ts": self.ts,
            "type": self.type.value if isinstance(self.type, Enum) else self.type,
            "payload": self.payload,
        }

    def to_row(self) -> tuple:
        return (self.run_id, self.turn, self.ts, self.type.value, json.dumps(self.payload, ensure_ascii=False, default=str))


SCHEMA_RUNS = """CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    task        TEXT,
    workdir     TEXT,
    config_json TEXT,
    status      TEXT,
    created_at  REAL
)"""

SCHEMA_EVENTS = """CREATE TABLE IF NOT EXISTS events (
    run_id TEXT,
    turn   INTEGER,
    ts     REAL,
    type   TEXT,
    payload TEXT
)"""


@dataclass
class Run:
    run_id: str
    task: str
    workdir: str
    config_json: str
    status: str
    created_at: float

    @classmethod
    def from_events(cls, run_id: str, config: AgentConfig, events: list[Event]) -> Run:
        run_start = next((e for e in events if e.type == EventType.run_start), None)
        run_end = next((e for e in events if e.type == EventType.run_end), None)
        start_ts = run_start.ts if run_start else time.time()
        status = run_end.payload.get("reason", "in_progress") if run_end else "in_progress"
        payload = run_start.payload if run_start else {}
        return cls(
            run_id=run_id,
            task=payload.get("task", ""),
            workdir=payload.get("workdir", ""),
            config_json=json.dumps(config.to_public_dict(), ensure_ascii=False),
            status=status,
            created_at=start_ts,
        )

    def to_row(self) -> tuple:
        return (self.run_id, self.task, self.workdir, self.config_json, self.status, self.created_at)


@dataclass
class Trajectory:
    run_id: str
    config: AgentConfig
    events: list[Event] = field(default_factory=list)

    def __post_init__(self):
        self._persisted_count = 0

    def emit(self, type: EventType, turn: int | None = None, payload: dict | None = None) -> Event:
        ev = Event(
            run_id=self.run_id,
            turn=turn,
            ts=time.time(),
            type=type,
            payload=dict(payload or {}),
        )
        self.events.append(ev)
        return ev

    def to_messages(self) -> list[Message]:
        messages: list[Message] = []
        pending_tool_results: list[ToolResultBlock] = []

        def flush_results():
            if pending_tool_results:
                messages.append(Message(role="user", content=cast(list[Block], pending_tool_results[:])))
                pending_tool_results.clear()

        for ev in self.events:
            if ev.type == EventType.run_start:
                if messages and messages[-1].role == "user":
                    continue
                messages.append(Message(role="user", content=ev.payload.get("task", "")))
            elif ev.type == EventType.llm_response:
                flush_results()
                blocks: list[Block] = []
                for b in ev.payload.get("blocks", []):
                    try:
                        decoded = _decode_block(b)
                    except ValueError:
                        decoded = None
                    if decoded:
                        blocks.append(decoded)
                if blocks:
                    messages.append(Message(role="assistant", content=blocks))
            elif ev.type == EventType.tool_result:
                try:
                    pending_tool_results.append(
                        ToolResultBlock(
                            tool_use_id=ev.payload.get("tool_use_id", ""),
                            content=ev.payload.get("content", ""),
                            is_error=ev.payload.get("is_error", False),
                        )
                    )
                except ValueError:
                    pass

        flush_results()
        return messages

    def export_jsonl(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for ev in self.events:
                f.write(json.dumps(ev.to_dict(), ensure_ascii=True) + "\n")

    def persist(self, path: str | Path | None = None) -> None:
        db_path = Path(path or self.config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(SCHEMA_RUNS)
            conn.execute(SCHEMA_EVENTS)

            new_events = self.events[self._persisted_count:]
            if new_events:
                run = Run.from_events(self.run_id, self.config, self.events)
                conn.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?)", run.to_row())
                conn.executemany(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    [ev.to_row() for ev in new_events],
                )
                conn.commit()
                self._persisted_count = len(self.events)
        finally:
            if conn:
                conn.close()


def _decode_block(d: dict) -> Block | None:
    if not isinstance(d, dict):
        return None
    t = d.get("type")
    if t == "text":
        return TextBlock(text=d.get("text", ""))
    elif t == "thinking":
        return ThinkingBlock(text=d.get("text", ""))
    elif t == "tool_use":
        return ToolUseBlock(
            id=d.get("id", ""),
            name=d.get("name", ""),
            input=d.get("input", {}),
        )
    elif t == "tool_result":
        return ToolResultBlock(
            tool_use_id=d.get("tool_use_id", ""),
            content=d.get("content", ""),
            is_error=d.get("is_error", False),
        )
    return None

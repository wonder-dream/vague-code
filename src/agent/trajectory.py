from __future__ import annotations

import json
import sqlite3
import time
import warnings
from dataclasses import dataclass, field, fields as dc_fields
from enum import Enum
from pathlib import Path
from typing import cast

from src.agent.config import AgentConfig, TransportConfig
from src.agent.context import SystemPrompt
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
    stream_event = "stream_event"
    retry = "retry"
    retry_divergence = "retry_divergence"
    compression = "compression"
    permission_check = "permission_check"
    mode_change = "mode_change"


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
        try:
            payload_json = json.dumps(self.payload, ensure_ascii=False)
        except TypeError:
            import warnings
            warnings.warn(
                f"Payload for event type '{self.type.value}' contains non-serializable data; "
                f"falling back to repr() — roundtrip will be lossy.",
                stacklevel=2,
            )
            payload_json = json.dumps(self.payload, ensure_ascii=False, default=str)
        return (self.run_id, self.turn, self.ts, self.type.value, payload_json)


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

SCHEMA_INDEX_EVENTS = "CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id)"


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

    @classmethod
    def from_db(cls, run_id: str, db_path: str) -> Trajectory:
        conn = sqlite3.connect(db_path)
        try:
            try:
                row = conn.execute(
                    "SELECT config_json, status FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                raise ValueError(f"Run {run_id} not found in {db_path}")
            if row is None:
                raise ValueError(f"Run {run_id} not found in {db_path}")
            config_data = json.loads(row[0])
            transport_data = config_data.pop("transport", {})

            agent_keys = {f.name for f in dc_fields(AgentConfig) if f.name != "transport"}
            filtered = {k: v for k, v in config_data.items() if k in agent_keys}
            skipped = set(config_data) - agent_keys
            if skipped:
                warnings.warn(f"from_db: ignoring unknown AgentConfig fields: {', '.join(sorted(skipped))}", stacklevel=2)

            transport_keys = {f.name for f in dc_fields(TransportConfig)}
            filtered_t = {k: v for k, v in transport_data.items() if k in transport_keys}
            skipped_t = set(transport_data) - transport_keys
            if skipped_t:
                warnings.warn(f"from_db: ignoring unknown TransportConfig fields: {', '.join(sorted(skipped_t))}", stacklevel=2)

            config = AgentConfig(**filtered)
            config.transport = TransportConfig(**filtered_t)
            compression_data = config_data.get("compression")
            if compression_data and isinstance(compression_data, dict):
                from src.agent.config import CompressionConfig as CC
                comp_keys = {f.name for f in dc_fields(CC)}
                filtered_c = {k: v for k, v in compression_data.items() if k in comp_keys}
                config.compression = CC(**filtered_c)
            memory_data = config_data.get("memory")
            if memory_data and isinstance(memory_data, dict):
                from src.agent.config import MemoryConfig as MC
                mem_keys = {f.name for f in dc_fields(MC)}
                filtered_m = {k: v for k, v in memory_data.items() if k in mem_keys}
                config.memory = MC(**filtered_m)
            traj = cls(run_id=run_id, config=config)
            for row in conn.execute(
                "SELECT turn, ts, type, payload FROM events WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ):
                traj.events.append(Event(
                    run_id=run_id,
                    turn=row[0],
                    ts=row[1],
                    type=EventType(row[2]),
                    payload=json.loads(row[3]),
                ))
            traj._persisted_count = len(traj.events)
            return traj
        finally:
            conn.close()

    def emit(self, type: EventType, turn: int | None = None, payload: dict | None = None, *, ts: float | None = None) -> Event:
        ev = Event(
            run_id=self.run_id,
            turn=turn,
            ts=ts if ts is not None else time.time(),
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
                workdir = ev.payload.get("workdir", "")
                if workdir and not any(m.role == "system" for m in messages):
                    sys_text = SystemPrompt(workdir).build()
                    messages.append(Message(role="system", content=sys_text))
                task = ev.payload.get("task", "")
                messages.append(Message(role="user", content=task))
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
            conn.execute(SCHEMA_INDEX_EVENTS)

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
        return ThinkingBlock(text=d.get("text", ""), signature=d.get("signature"))
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

"""Parallel session management for the vague-code TUI (ADR-0026).

Each session owns its Agent instance, transcript, worker, and guidance queue.
The manager routes app-level operations to the current session.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vague_code.agent.backend import ModelBackend
from vague_code.agent.loop import Agent
from vague_code.tui.state import TuiTranscript, TuiTranscriptEntry


@dataclass
class SessionState:
    run_id: str
    title: str
    transcript: TuiTranscript
    agent: Agent | None = None
    busy: bool = False
    worker: object | None = None
    active_token: int | None = None
    pending_guidance: list[str] = field(default_factory=list)
    offline_tools: dict[str, TuiTranscriptEntry] = field(default_factory=dict)
    resume_run_id: str | None = None
    # 会话级模型状态（ADR-0039）：各会话独立 provider/model/backend
    provider: str = ""
    model: str = ""
    backend: ModelBackend | None = None


class SessionManager:
    """Owns all live sessions; exactly one is current (rendered in the output view)."""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self.current_run_id: str | None = None

    @property
    def current(self) -> SessionState | None:
        if self.current_run_id is None:
            return None
        return self.sessions.get(self.current_run_id)

    def create(self, run_id: str, title: str) -> SessionState:
        state = SessionState(run_id=run_id, title=title, transcript=TuiTranscript())
        self.sessions[run_id] = state
        self.current_run_id = run_id
        return state

    def switch(self, run_id: str) -> SessionState | None:
        state = self.sessions.get(run_id)
        if state is None:
            return None
        self.current_run_id = run_id
        return state

    def rename(self, old: str, new: str) -> None:
        """Re-key a session after its real run_id is known (first chat turn)."""
        state = self.sessions.pop(old, None)
        if state is None:
            return
        state.run_id = new
        self.sessions[new] = state
        if self.current_run_id == old:
            self.current_run_id = new

    def remove(self, run_id: str) -> bool:
        """Drop a session from memory. Returns True if it was the current one."""
        self.sessions.pop(run_id, None)
        if self.current_run_id != run_id:
            return False
        self.current_run_id = next(iter(self.sessions), None)
        return True

    def get(self, run_id: str) -> SessionState | None:
        return self.sessions.get(run_id)

    def active_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.busy)

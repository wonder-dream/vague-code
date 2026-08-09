"""App-level interaction tests: real keyboard paths (Textual Pilot).

These tests exercise the composer / picker / permission-dialog flows through
the actual key handlers instead of calling app internals directly, so that
regressions like the initial-focus or picker-Enter bugs are caught.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from src.agent.config import AgentConfig
from src.agent.ir import MessageEnd, MessageStart, StopReason, TextDelta
from src.agent.permission import Decision, Operation
from src.tui.app import XClawApp
from src.tui.screens.permission import PermissionDialog
from src.tui.state import TuiEntryKind


class _FakeBackend:
    name = "fake"


class _FakeTrajectory:
    run_id = "fake-run"
    events = []


class _InteractionApp(XClawApp):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.submitted: list[str] = []
        self.resumed: list[str] = []
        self.permission_decision: Decision | None = None

    def _run_agent_worker(self, state, text: str, token: int) -> None:
        self.submitted.append(text)
        self.call_from_thread(self._on_run_complete, _FakeTrajectory(), state, token)

    def _run_resume_worker(self, run_id: str, traj, token: int) -> None:
        self.resumed.append(run_id)


class _PermissionApp(_InteractionApp):
    def _run_agent_worker(self, state, text: str, token: int) -> None:
        op = Operation("write_file", {"path": "a.py", "content": "x"})
        decision = self._thread_permission(op, Decision.CONFIRM)
        self.permission_decision = decision
        self.call_from_thread(self._on_run_complete, _FakeTrajectory(), state, token)


def _make_app(cls=_InteractionApp, *, db_path=str(Path(tempfile.mkdtemp()) / "runs.db"), **kwargs):
    config = AgentConfig(model="m", max_turns=2, db_path=db_path)
    config.permission_mode = "normal"
    return cls(config=config, backend=_FakeBackend(), workdir=".", **kwargs)


def _make_db(tmp_path) -> str:
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT, task TEXT, status TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?)",
        ("run1", "fix the bug", "end_turn", "2026-08-01T00:00:00"),
    )
    conn.commit()
    conn.close()
    return str(db)


async def test_initial_focus_is_composer() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert getattr(app.focused, "id", None) == "input"


async def test_submit_via_keys_creates_user_entry() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.submitted == ["hi"]
        kinds = [e.kind for e in app.transcript.entries]
        assert TuiEntryKind.USER in kinds
        user_entries = [e for e in app.transcript.entries if e.kind == TuiEntryKind.USER]
        assert user_entries[-1].body == "hi"


async def test_picker_enter_selects_highlighted(tmp_path, monkeypatch) -> None:
    app = _make_app(db_path=_make_db(tmp_path))

    class _DummyTraj:
        events = []

    monkeypatch.setattr("src.agent.trajectory.Trajectory.from_db", lambda run_id, db_path: _DummyTraj())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("/resume")
        await pilot.pause(0.2)
        assert app._picker is not None
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app._picker is None
        assert app.resumed == ["run1"]


async def test_picker_digit_selects_number(tmp_path, monkeypatch) -> None:
    app = _make_app(db_path=_make_db(tmp_path))

    class _DummyTraj:
        events = []

    monkeypatch.setattr("src.agent.trajectory.Trajectory.from_db", lambda run_id, db_path: _DummyTraj())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("/resume")
        await pilot.pause(0.2)
        assert app._picker is not None
        await pilot.press("1")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app._picker is None
        assert app.resumed == ["run1"]


async def test_picker_escape_cancels(tmp_path) -> None:
    app = _make_app(db_path=_make_db(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("/resume")
        await pilot.pause(0.2)
        assert app._picker is not None
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app._picker is None
        assert app.resumed == []


async def test_permission_dialog_focuses_allow_and_y_allows() -> None:
    app = _make_app(cls=_PermissionApp)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause(0.5)
        assert isinstance(app.screen, PermissionDialog)
        assert getattr(app.focused, "id", None) == "perm-allow"
        await pilot.press("y")
        await pilot.pause(0.5)
        assert app.permission_decision == Decision.ALLOW


async def test_permission_dialog_escape_denies() -> None:
    app = _make_app(cls=_PermissionApp)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause(0.5)
        assert isinstance(app.screen, PermissionDialog)
        await pilot.press("escape")
        await pilot.pause(0.5)
        assert app.permission_decision == Decision.DENY


async def test_css_path_resolves_to_package_file() -> None:
    from pathlib import Path

    assert Path(XClawApp.CSS_PATH).is_file()


async def test_reasoning_status_cleared_after_finalize() -> None:
    from src.agent.ir import ThinkingDelta, ThinkingEnd, ThinkingStart

    app = _make_app()

    class _ReasoningApp(_InteractionApp):
        def _run_agent_worker(self, state, text: str, token: int) -> None:
            for ev in [
                MessageStart(model="m"),
                ThinkingStart(),
                ThinkingDelta(delta="short thought"),
                ThinkingEnd(signature=None),
                TextDelta(delta="answer"),
                MessageEnd(stop_reason=StopReason.end_turn),
            ]:
                self.call_from_thread(self._on_stream_event, ev, state, token)
            self.call_from_thread(self._on_run_complete, _FakeTrajectory(), state, token)

    app = _make_app(cls=_ReasoningApp)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause(0.5)
        reasoning = [e for e in app.transcript.entries if e.kind == TuiEntryKind.REASONING]
        assert reasoning and reasoning[-1].status is None
        assert reasoning[-1].body == "short thought"


# ── chat session (ADR-0025): real runner path, fake agent via _new_agent ────

class _ChatFakeTrajectory:
    def __init__(self) -> None:
        self.run_id = "fake-run"
        self.events = []


class _ChatFakeHandle:
    def __init__(self) -> None:
        self.trajectory = _ChatFakeTrajectory()

    def __iter__(self):
        return iter([
            MessageStart(model="m"),
            TextDelta(delta="reply"),
            MessageEnd(stop_reason=StopReason.end_turn),
        ])

    def close(self) -> None:
        pass


class _ChatFakeAgent:
    def __init__(self) -> None:
        self.chat_calls: list[str] = []
        self.resume_calls: list[str] = []
        self.ended = 0
        self._seq = 0

    def chat(self, task: str, workdir: str):
        self.chat_calls.append(task)
        self._seq += 1
        handle = _ChatFakeHandle()
        handle.trajectory.run_id = f"fake-run-{self._seq}"
        return handle

    def chat_resume(self, run_id: str):
        self.resume_calls.append(run_id)
        self._seq += 1
        handle = _ChatFakeHandle()
        handle.trajectory.run_id = f"fake-run-{self._seq}"
        return handle

    def chat_end(self) -> None:
        self.ended += 1

    def add_permission_rule(self, pattern: str, action: str = "allow") -> None:
        pass


async def test_two_chat_turns_share_session(monkeypatch) -> None:
    from src.agent.loop import Agent

    class _RealChatApp(XClawApp):
        pass

    fake = _ChatFakeAgent()
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app(cls=_RealChatApp)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._submit_task("second")
        await pilot.pause(0.6)
        assert len(app._sessions.sessions) == 1
        state = app._sessions.current
        assert fake.chat_calls == ["first", "second"]
        users = [e for e in state.transcript.entries if e.kind == TuiEntryKind.USER]
        assert [e.body for e in users] == ["first", "second"]
        assert not any(e.kind == TuiEntryKind.ERROR for e in state.transcript.entries)


async def test_new_session_creates_parallel_session(monkeypatch) -> None:
    from src.agent.loop import Agent

    class _RealChatApp(XClawApp):
        pass

    fake = _ChatFakeAgent()
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app(cls=_RealChatApp)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._handle_slash("/new")
        await pilot.pause(0.2)
        assert len(app._sessions.sessions) == 2
        app._submit_task("second")
        await pilot.pause(0.6)
        assert fake.chat_calls == ["first", "second"]
        assert len(app._sessions.sessions) == 2

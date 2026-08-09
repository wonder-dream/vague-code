"""Parallel session tests (ADR-0026): concurrent chat, switching, title summary."""

from __future__ import annotations
from pathlib import Path

import tempfile

import threading
import time

from src.agent.config import AgentConfig
from src.agent.ir import MessageEnd, MessageStart, StopReason, TextDelta
from src.agent.loop import Agent
from src.agent.permission import Decision
from src.tui.app import XClawApp
from src.tui.screens.permission import PermissionDialog
from src.tui.state import TuiEntryKind
from src.tui.widgets.sidebar import SessionSidebar
from textual.widgets import Static


class _FakeBackend:
    name = "fake"


class _FakeTrajectory:
    def __init__(self, run_id: str, task: str = "") -> None:
        self.run_id = run_id
        self.events = (
            [type("E", (), {"type": "run_start", "payload": {"task": task}})()]
            if task
            else []
        )


class _FakeHandle:
    def __init__(self, run_id: str, task: str = "") -> None:
        self.trajectory = _FakeTrajectory(run_id, task)

    def __iter__(self):
        return iter([
            MessageStart(model="m"),
            TextDelta(delta="reply"),
            MessageEnd(stop_reason=StopReason.end_turn),
        ])

    def close(self) -> None:
        pass


class _ParallelAgent:
    """Fake agent tracking concurrent chat calls (deterministic via Events)."""

    def __init__(self, parallel: bool = True) -> None:
        self.chat_calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.summarize_calls = 0
        self._parallel = parallel
        self._first_entered = threading.Event()
        self._second_entered = threading.Event()

    def chat(self, task: str, workdir: str):
        run_id = f"run-{len(self.chat_calls) + 1}"
        self.chat_calls.append(task)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self._parallel and len(self.chat_calls) == 1:
            self._first_entered.set()
            self._second_entered.wait(5.0)
        elif self._parallel:
            self._first_entered.wait(5.0)
            self._second_entered.set()
            self._first_entered.clear()
        time.sleep(0.05)
        self.active -= 1
        return _FakeHandle(run_id, task)

    def summarize(self, task: str, reply: str, max_chars: int = 15) -> str:
        self.summarize_calls += 1
        return (task or "会话")[:max_chars]


def _make_app(cls=XClawApp, **kwargs):
    config = AgentConfig(model="m", max_turns=5, db_path=str(Path(tempfile.mkdtemp()) / "runs.db"))
    config.permission_mode = "normal"
    return cls(config=config, backend=_FakeBackend(), workdir=".", **kwargs)


async def test_two_sessions_run_in_parallel(monkeypatch) -> None:
    fake = _ParallelAgent()
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.1)
        app._handle_slash("/new")
        await pilot.pause(0.1)
        app._submit_task("second")
        await pilot.pause(0.8)
        assert len(app._sessions.sessions) == 2
        assert fake.chat_calls == ["first", "second"]
        assert fake.max_active == 2, "sessions must run concurrently"


async def test_switch_session_renders_target_transcript(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.5)
        app._handle_slash("/new")
        await pilot.pause(0.1)
        app._submit_task("second")
        await pilot.pause(0.5)
        sessions = list(app._sessions.sessions.values())
        assert len(sessions) == 2
        state_a, state_b = sessions[0], sessions[1]
        assert state_a.transcript.entries and state_b.transcript.entries
        app._switch_session(state_a.run_id)
        await pilot.pause(0.2)
        output = app.query_one("#output")
        assert output.transcript is state_a.transcript
        users = [e for e in output.transcript.entries if e.kind == TuiEntryKind.USER]
        assert users[0].body == "first"
        app._switch_session(state_b.run_id)
        await pilot.pause(0.2)
        assert app.query_one("#output").transcript is state_b.transcript


async def test_sidebar_lists_sessions_and_selects(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.5)
        app._handle_slash("/new")
        await pilot.pause(0.1)
        app._submit_task("second")
        await pilot.pause(0.5)
        sidebar = app.query_one("#sidebar", SessionSidebar)
        assert "hidden" in sidebar.classes
        app.action_toggle_sidebar()
        await pilot.pause(0.2)
        assert "hidden" not in sidebar.classes
        assert len(sidebar._items) >= 2
        state_b = list(app._sessions.sessions.values())[1]
        target = state_b.run_id
        app._switch_session(target)
        await pilot.pause(0.2)
        assert app._sessions.current_run_id == target


async def test_title_summary_generated_on_first_turn(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    monkeypatch.setattr(Agent, "summarize", fake.summarize)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("帮我修这个 bug")
        await pilot.pause(0.8)
        state = app._sessions.current
        assert fake.summarize_calls >= 1
        assert state.title == "帮我修这个 bug"[:15]


async def test_permission_requests_serialize_across_sessions(monkeypatch) -> None:
    from src.agent.permission import Operation

    calls: list[str] = []
    decisions: list = []
    first_entered = threading.Event()
    second_entered = threading.Event()

    def wrapped_chat(self, task: str, workdir: str):
        run_id = f"run-{len(calls) + 1}"
        calls.append(task)
        if len(calls) == 1:
            first_entered.set()
            second_entered.wait(5.0)
        else:
            first_entered.wait(5.0)
            second_entered.set()
            first_entered.clear()
        op = Operation("write_file", {"path": "a.py", "content": "x"})
        decisions.append(self._on_permission(op, Decision.CONFIRM))
        return _FakeHandle(run_id, task)

    monkeypatch.setattr(Agent, "chat", wrapped_chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.3)
        app._handle_slash("/new")
        await pilot.pause(0.3)
        app._submit_task("second")
        for _ in range(40):
            if isinstance(app.screen, PermissionDialog):
                break
            await pilot.pause(0.1)
        assert isinstance(app.screen, PermissionDialog)
        await pilot.press("y")
        for _ in range(40):
            if len(decisions) >= 1:
                break
            await pilot.pause(0.1)
        for _ in range(40):
            if isinstance(app.screen, PermissionDialog):
                break
            await pilot.pause(0.1)
        assert isinstance(app.screen, PermissionDialog)
        await pilot.press("y")
        for _ in range(40):
            if len(decisions) >= 2:
                break
            await pilot.pause(0.1)
        assert decisions == [Decision.ALLOW, Decision.ALLOW]


async def test_cold_session_message_lazily_builds_agent(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)

    class _FakeTraj:
        events = [
            type("E", (), {"type": "run_start", "payload": {"mode": "chat", "task": "旧任务"}})(),
        ]

    monkeypatch.setattr(
        "src.agent.trajectory.Trajectory.from_db",
        lambda run_id, db_path: _FakeTraj(),
    )
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._switch_session("cold-run-123")
        await pilot.pause(0.2)
        state = app._sessions.current
        assert state is not None and state.run_id == "cold-run-123"
        assert state.agent is None
        app._submit_task("你好吗")
        await pilot.pause(0.6)
        assert state.agent is not None
        assert fake.chat_calls[-1] == "你好吗"


async def test_sidebar_delete_requires_confirmation(monkeypatch) -> None:
    from src.tui.screens.confirm import ConfirmDialog

    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        run_id = app._sessions.current_run_id
        sidebar = app.query_one("#sidebar", SessionSidebar)
        sidebar.move_selection(0)
        app._prompt_delete_session()
        await pilot.pause(0.3)
        assert isinstance(app.screen, ConfirmDialog)
        # cancel with N: session stays
        await pilot.press("n")
        await pilot.pause(0.3)
        assert run_id in app._sessions.sessions
        # delete with Y: session gone from memory and DB
        sidebar.move_selection(0)
        app._prompt_delete_session()
        await pilot.pause(0.3)
        await pilot.press("y")
        await pilot.pause(0.4)
        assert run_id not in app._sessions.sessions
        import sqlite3
        conn = sqlite3.connect("runs/runs.db")
        try:
            row = conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
        finally:
            conn.close()
        assert row is None


async def test_sidebar_delete_current_switches_to_other_session(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._handle_slash("/new")
        await pilot.pause(0.2)
        app._submit_task("second")
        await pilot.pause(0.6)
        first_id = list(app._sessions.sessions.keys())[0]
        second_id = list(app._sessions.sessions.keys())[1]
        app._switch_session(first_id)
        await pilot.pause(0.2)
        app._delete_session(first_id)
        await pilot.pause(0.3)
        assert first_id not in app._sessions.sessions
        assert app._sessions.current_run_id == second_id


async def test_busy_session_cannot_be_deleted(monkeypatch) -> None:
    fake = _ParallelAgent()
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.1)
        run_id = app._sessions.current_run_id
        app._prompt_delete_session()
        await pilot.pause(0.3)
        from src.tui.screens.confirm import ConfirmDialog
        assert not isinstance(app.screen, ConfirmDialog)
        assert run_id in app._sessions.sessions


async def test_sidebar_mouse_click_switches_session(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._handle_slash("/new")
        await pilot.pause(0.2)
        app._submit_task("second")
        await pilot.pause(0.6)
        sessions = list(app._sessions.sessions.values())
        state_a = sessions[0]
        sidebar = app.query_one("#sidebar", SessionSidebar)
        app.action_toggle_sidebar()
        await pilot.pause(0.2)
        rows = [
            w for w in sidebar.query("#session-rows > Static")
            if isinstance(w, Static)
        ]
        assert len(rows) >= 2
        target = state_a.run_id
        target_row = next(r for r in rows if r.run_id == target)
        await pilot.click(target_row)
        await pilot.pause(0.3)
        assert app._sessions.current_run_id == target
        assert sidebar.selected_index == rows.index(target_row)


async def test_sidebar_delete_button_click(monkeypatch) -> None:
    from rich.text import Text

    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._handle_slash("/new")
        await pilot.pause(0.2)
        app._submit_task("second")
        await pilot.pause(0.6)
        sessions = list(app._sessions.sessions.values())
        target = sessions[0].run_id
        sidebar = app.query_one("#sidebar", SessionSidebar)
        app.action_toggle_sidebar()
        await pilot.pause(0.2)
        rows = [
            w for w in sidebar.query("#session-rows > Static")
            if isinstance(w, Static) and getattr(w, "run_id", None) == target
        ]
        assert rows, "target row not found"
        row = rows[0]
        # 点击行尾 ✕ 区域（最后 2 列，cell 宽安全）
        rendered = row.render()
        text = getattr(rendered, "text", None) or str(rendered)
        cell_len = Text.from_markup(text).cell_len

        class _Click:
            def __init__(self, x, widget):
                self.x = x
                self.y = 0
                self.widget = widget

        sidebar.on_click(_Click(cell_len - 1, row))
        await pilot.pause(0.3)
        from src.tui.screens.confirm import ConfirmDialog
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("n")
        await pilot.pause(0.3)
        assert target in app._sessions.sessions
        assert app.focused is sidebar or (app.focused is not None and app.focused.id == "sidebar")


async def test_confirm_delete_keeps_focus_on_sidebar(monkeypatch) -> None:
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        sidebar = app.query_one("#sidebar", SessionSidebar)
        app.action_toggle_sidebar()
        await pilot.pause(0.2)
        sidebar.move_selection(0)
        app._prompt_delete_session()
        await pilot.pause(0.3)
        from src.tui.screens.confirm import ConfirmDialog
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("n")
        await pilot.pause(0.4)
        assert app.focused is not None and app.focused.id == "sidebar"


async def test_deleted_session_row_removed_from_sidebar(monkeypatch) -> None:
    """删除会话后，侧边栏必须移除该行，点击残留行不得再触发切换。"""
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        app._handle_slash("/new")
        await pilot.pause(0.2)
        app._submit_task("second")
        await pilot.pause(0.6)
        sessions = list(app._sessions.sessions.values())
        first_id = sessions[0].run_id
        second_id = sessions[1].run_id
        sidebar = app.query_one("#sidebar", SessionSidebar)
        app.action_toggle_sidebar()
        await pilot.pause(0.2)
        assert len(sidebar._rows) == 2

        # 删除第一个会话（直接走内部路径，绕过 DB，因为 fake 会话未持久化）
        app._delete_session(first_id)
        await pilot.pause(0.4)
        assert first_id not in app._sessions.sessions
        # 行数必须收缩
        assert len(sidebar._rows) == 1
        remaining_run_ids = [
            getattr(r, "run_id", None) for r in sidebar._rows
        ]
        assert first_id not in remaining_run_ids
        assert remaining_run_ids == [second_id]


async def test_delete_session_cleans_artifacts(tmp_path) -> None:
    """删除会话必须自动清理：DB 行、轨迹 jsonl、memory.db 记忆。"""
    import sqlite3 as _sq

    db_path = str(tmp_path / "runs.db")
    mem_path = str(tmp_path / "memory.db")
    run_id = "abcdef123456"

    # 构造会话数据：runs + events + jsonl 轨迹 + memory 记忆
    conn = _sq.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, task TEXT, status TEXT, created_at REAL);
        CREATE TABLE events (run_id TEXT, turn INTEGER, ts REAL, type TEXT, payload TEXT);
        """
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, 'some task', 'end_turn', 1.0)", (run_id,)
    )
    conn.execute(
        "INSERT INTO events VALUES (?, 0, 1.0, 'run_start', '{}')", (run_id,)
    )
    conn.commit()
    conn.close()

    traj_file = tmp_path / f"{run_id}.jsonl"
    traj_file.write_text('{"run_id": "%s"}\n' % run_id, encoding="utf-8")

    mconn = _sq.connect(mem_path)
    mconn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT, content TEXT, source_session TEXT,
            created_at TEXT, last_used_at TEXT, use_count INTEGER,
            confidence REAL, content_hash TEXT UNIQUE
        );
        """
    )
    mconn.execute(
        "INSERT INTO memories (kind, content, source_session, created_at, last_used_at, content_hash) "
        "VALUES ('episodic', 'ctx summary', ?, 'now', 'now', 'h1')",
        (run_id,),
    )
    mconn.commit()
    mconn.close()

    config = AgentConfig(model="m", max_turns=5, db_path=db_path)
    config.memory.memory_db_path = mem_path
    app = XClawApp(config=config, backend=_FakeBackend(), workdir=".")

    async with app.run_test() as pilot:
        await pilot.pause()
        app._delete_session(run_id)
        await pilot.pause(0.3)

    # DB 行删除
    conn = _sq.connect(db_path)
    try:
        assert conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM events WHERE run_id=?", (run_id,)).fetchone() is None
    finally:
        conn.close()
    # jsonl 删除
    assert not traj_file.exists()
    # memory 删除
    mconn = _sq.connect(mem_path)
    try:
        assert mconn.execute("SELECT 1 FROM memories WHERE source_session=?", (run_id,)).fetchone() is None
    finally:
        mconn.close()


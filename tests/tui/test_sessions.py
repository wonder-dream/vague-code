"""Parallel session tests (ADR-0026): concurrent chat, switching, title summary."""

from __future__ import annotations
from pathlib import Path

import uuid

import threading
import time

from vague_code.agent.config import AgentConfig
from vague_code.agent.ir import MessageEnd, MessageStart, StopReason, TextDelta
from vague_code.agent.loop import Agent
from vague_code.agent.permission import Decision
from vague_code.tui.app import VagueCodeApp
from vague_code.tui.screens.permission import PermissionDialog
from vague_code.tui.state import TuiEntryKind
from vague_code.tui.widgets.sidebar import SessionSidebar
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



def _tmp_dir() -> Path:
    root = Path(__file__).resolve().parent.parent.parent / ".testtmp_tui"
    root.mkdir(parents=True, exist_ok=True)
    p = root / ("sess_" + uuid.uuid4().hex[:12])
    p.mkdir(parents=True, exist_ok=True)
    return p

def _make_app(cls=VagueCodeApp, **kwargs):
    config = AgentConfig(model="m", max_turns=5, db_path=str(Path(_tmp_dir()) / "runs.db"))
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
    from vague_code.agent.permission import Operation

    calls: list[str] = []
    decisions: list = []
    first_entered = threading.Event()
    second_entered = threading.Event()

    def _approve() -> None:
        """直接 dismiss 决策（绕过按键/焦点时序；on_key 内部同样是 dismiss）。"""
        dialog = app.screen
        dialog.dismiss(Decision.ALLOW)

    def wrapped_chat(self, task: str, workdir: str):
        run_id = f"run-{len(calls) + 1}"
        calls.append(task)
        if len(calls) == 1:
            first_entered.set()
            second_entered.wait(10.0)
        else:
            first_entered.wait(10.0)
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
        for _ in range(100):
            if isinstance(app.screen, PermissionDialog):
                break
            await pilot.pause(0.1)
        assert isinstance(app.screen, PermissionDialog)
        _approve()
        for _ in range(100):
            if len(decisions) >= 1:
                break
            await pilot.pause(0.1)
        for _ in range(100):
            if isinstance(app.screen, PermissionDialog):
                break
            await pilot.pause(0.1)
        assert isinstance(app.screen, PermissionDialog)
        _approve()
        for _ in range(100):
            if len(decisions) >= 2:
                break
            await pilot.pause(0.1)
        assert decisions == [Decision.ALLOW, Decision.ALLOW]


async def test_cold_session_message_resumes_same_run(monkeypatch) -> None:
    """B2: 加载历史 chat 会话后输入消息 → chat_resume 接续原 run（而非开新 run）。"""
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    resume_calls: list[str] = []
    monkeypatch.setattr(
        Agent, "chat_resume",
        lambda self, run_id: (resume_calls.append(run_id), _FakeHandle(run_id, "旧任务"))[1],
    )

    class _FakeTraj:
        events = [
            type("E", (), {"type": "run_start", "payload": {"mode": "chat", "task": "旧任务"}})(),
        ]

    monkeypatch.setattr(
        "vague_code.agent.trajectory.Trajectory.from_db",
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
        assert state.agent is not None
        assert state.resume_run_id == "cold-run-123"
        app._submit_task("你好吗")
        await pilot.pause(0.6)
        assert resume_calls == ["cold-run-123"]
        assert state.resume_run_id is None


async def test_no_stale_worker_key_after_first_turn(monkeypatch) -> None:
    """B6: 首轮结束后占位 run_id 键无残留（rename 后 remap + finally pop 对齐）。"""
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.8)
        state = app._sessions.current
        assert state is not None
        assert app._session_workers.get(state.run_id) is None
        assert len(app._session_workers) == 0


class _FakeWorker:
    """模拟 Textual worker：state 可任意指定。"""

    class _State:
        def __init__(self, name: str) -> None:
            self.name = name

    def __init__(self, name: str = "RUNNING") -> None:
        self.state = self._State(name)
        self.is_cancelled = name == "CANCELLED"


async def test_submit_while_worker_lingering_queues(monkeypatch) -> None:
    """B4: 旧 worker 已 cancel 但线程仍在收尾时提交 → 入队而非开第二个 worker。"""
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        state = app._sessions.current
        assert fake.chat_calls == ["first"]
        app._session_workers[state.run_id] = _FakeWorker("CANCELLED")
        app._submit_task("queued while lingering")
        await pilot.pause(0.3)
        assert fake.chat_calls == ["first"]  # 未启动新 worker
        assert state.pending_guidance == ["queued while lingering"]


async def test_queued_turn_auto_starts_after_worker_exit(monkeypatch) -> None:
    """B4: 旧 worker 退出（finally）后，排队消息自动开始下一轮。"""
    fake = _ParallelAgent(parallel=False)
    monkeypatch.setattr(Agent, "chat", fake.chat)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first")
        await pilot.pause(0.6)
        state = app._sessions.current
        assert fake.chat_calls == ["first"]
        app._add_guidance(state, "queued text")
        pending = app._drain_guidance(state)
        app._start_queued_turn(state, "\n".join(pending))
        await pilot.pause(0.8)
        assert fake.chat_calls == ["first", "queued text"]
        assert state.pending_guidance == []


async def test_sidebar_delete_requires_confirmation(monkeypatch) -> None:
    from vague_code.tui.screens.confirm import ConfirmDialog

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
        from vague_code.tui.screens.confirm import ConfirmDialog
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
        from vague_code.tui.screens.confirm import ConfirmDialog
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
        from vague_code.tui.screens.confirm import ConfirmDialog
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
    """删除会话必须自动清理：DB 行、轨迹 jsonl、memory.md 记忆。"""
    import sqlite3 as _sq

    db_path = str(tmp_path / "runs.db")
    mem_dir = tmp_path / ".agent"
    mem_path = mem_dir / "memory.md"
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

    mem_dir.mkdir(parents=True, exist_ok=True)
    mem_path.write_text(
        "<!-- vague-code memory: agent 蒸馏的历史会话记忆，可手动编辑 -->\n\n"
        "## 目标会话记忆\n"
        "<!-- source: {0}; created: 2026-08-12; hash: aaa -->\n"
        "本次会话的要点\n\n"
        "## 其他会话记忆\n"
        "<!-- source: other123; created: 2026-08-12; hash: bbb -->\n"
        "保留的内容\n".format(run_id),
        encoding="utf-8",
    )

    config = AgentConfig(model="m", max_turns=5, db_path=db_path)
    config.memory.memory_file = str(mem_path.relative_to(tmp_path))
    app = VagueCodeApp(config=config, backend=_FakeBackend(), workdir=str(tmp_path))

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
    # memory 删除：目标会话分块移除，其他保留
    remaining = mem_path.read_text(encoding="utf-8")
    assert "目标会话记忆" not in remaining
    assert "其他会话记忆" in remaining



async def test_compact_summary_displayed_in_transcript() -> None:
    """ADR-0036: /compact 后摘要作为对话消息展示在 transcript。"""
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        summary = "## Goal\nfix the bug\n\n## Next Steps\n1. run tests"
        app._show_compact_summary(state, summary)
        await pilot.pause(0.1)
        assistants = [e for e in state.transcript.entries if e.kind == TuiEntryKind.ASSISTANT]
        assert assistants, "summary must render as an assistant-style entry"
        assert "[会话摘要]" in assistants[-1].body
        assert "## Goal" in assistants[-1].body


async def test_compact_worker_displays_summary(monkeypatch) -> None:
    """ADR-0036: _run_compact_worker 压缩成功后把摘要展示到对话流。"""
    monkeypatch.setattr(
        Agent, "compact_chat",
        lambda self: {"before": 10000, "after": 3000, "affected": 8,
                      "summary": "## Progress\n### Done\n- fixed bug"},
    )
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        app.run_worker(
            lambda: app._run_compact_worker(state),
            thread=True, exclusive=False, group="test-compact",
        )
        await pilot.pause(0.5)
        bodies = [e.body for e in state.transcript.entries]
        assert any("[会话摘要]" in b for b in bodies)
        assert any("fixed bug" in b for b in bodies)


# ── 会话级模型状态（ADR-0039）──────────────────────────────────────────────

def test_session_state_model_fields_default_empty() -> None:
    """新会话默认 provider/model/backend 为空字符串/None，互不影响。"""
    from vague_code.tui.session import SessionManager

    manager = SessionManager()
    a = manager.create("run-a", "A")
    b = manager.create("run-b", "B")
    assert a.provider == "" and a.model == "" and a.backend is None
    a.provider = "openai"
    a.model = "gpt-5.6-sol"
    a.backend = object()
    assert b.provider == "" and b.model == "" and b.backend is None
    assert a.provider == "openai"


async def test_model_switch_affects_only_current_session(monkeypatch) -> None:
    """ADR-0039：/model 同 provider 直切只作用于当前会话，其他会话不受影响。"""
    import vague_code.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_resolve_api_key", lambda env: "sk-" + env)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        a = app._begin_new_session()
        a.model = "deepseek-v4-flash"
        a.agent.config.model = "deepseek-v4-flash"
        app._handle_slash("/new")
        b = app._begin_new_session()
        b.model = "deepseek-v4-flash"
        b.agent.config.model = "deepseek-v4-flash"
        # 当前会话 B 切换模型
        app._handle_slash("/model deepseek-v4-pro")
        await pilot.pause()
        assert b.model == "deepseek-v4-pro"
        assert b.agent.config.model == "deepseek-v4-pro"
        assert a.model == "deepseek-v4-flash"
        assert a.agent.config.model == "deepseek-v4-flash"
        assert a.agent.config is not b.agent.config, "各会话 agent 配置必须独立"
        # topbar 跟随当前会话显示
        assert "deepseek-v4-pro" in app._topbar_text()
        app._switch_session(a.run_id)
        await pilot.pause()
        assert "deepseek-v4-flash" in app._topbar_text()


async def test_cross_provider_switch_with_key_direct(monkeypatch) -> None:
    """ADR-0039：跨 provider 且目标有 key → 会话级换 backend 直切。"""
    import vague_code.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_resolve_api_key", lambda env: "sk-" + env)
    created: list[str] = []

    def fake_build(provider, api_key, base_url, protocol, timeout_s, user_agent=None):
        created.append(provider)
        return type("NewBackend", (), {"name": "new"})()

    monkeypatch.setattr("vague_code.config.build_backend", fake_build)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        state.model = "deepseek-v4-flash"
        state.agent.config.model = "deepseek-v4-flash"
        old_backend = state.agent.backend
        app._handle_slash("/model gpt-5.6-sol")
        await pilot.pause()
        assert state.provider == "openai"
        assert state.model == "gpt-5.6-sol"
        assert state.agent.config.model == "gpt-5.6-sol"
        assert state.agent.backend is not old_backend
        assert state.backend.name == "new"
        assert created == ["openai"]


async def test_cross_provider_no_key_opens_wizard_and_escape_rolls_back(monkeypatch) -> None:
    """ADR-0039：跨 provider 无 key → 弹引导（预选目标），Esc 取消 → 会话零改动。"""
    import vague_code.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_resolve_api_key", lambda env: None)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        state.model = "deepseek-v4-flash"
        state.agent.config.model = "deepseek-v4-flash"
        app._handle_slash("/model gpt-5.6-sol")
        await pilot.pause(0.2)
        from vague_code.tui.screens.setup import SetupWizard
        assert isinstance(app.screen, SetupWizard)
        wizard = app.screen
        assert wizard._provider == "openai"
        assert wizard._preselect_model == "gpt-5.6-sol"
        assert wizard._cancellable is True
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert state.model == "deepseek-v4-flash"
        assert state.agent.config.model == "deepseek-v4-flash"
        assert state.backend is not None
        assert state.provider in ("", "deepseek")


async def test_cross_provider_wizard_finish_applies_session(monkeypatch, tmp_path) -> None:
    """ADR-0039：wizard 完成 → 写全局配置 + 会话级切换（agent backend 替换）。"""
    import json
    import vague_code.config as cfg_mod
    import vague_code.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_resolve_api_key", lambda env: None)
    fake_dir = tmp_path / "cfg"
    monkeypatch.setattr(cfg_mod, "global_config_dir", lambda: fake_dir)

    class _RealBackend:
        name = "built"

    monkeypatch.setattr(cfg_mod, "build_backend", lambda *a, **k: _RealBackend())
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        state.model = "deepseek-v4-flash"
        old_backend = state.agent.backend
        app._handle_slash("/model gpt-5.6-sol")
        await pilot.pause(0.2)
        wizard = app.screen
        wizard.query_one("#setup-key").value = "sk-openai"
        wizard._finish()
        await pilot.pause(0.3)
        assert state.provider == "openai"
        assert state.model == "gpt-5.6-sol"
        assert state.agent.config.model == "gpt-5.6-sol"
        assert state.agent.backend is not old_backend
        assert state.backend.name == "built"
        env_text = (fake_dir / ".env").read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=sk-openai" in env_text
        cfg = json.loads((fake_dir / "vague-code.json").read_text(encoding="utf-8"))
        assert cfg["defaultProvider"] == "openai"
        assert cfg["defaultModel"] == "gpt-5.6-sol"


async def test_two_sessions_keep_independent_model_backends(monkeypatch) -> None:
    """ADR-0039：会话 A deepseek / 会话 B openai 并行，互不干扰。"""
    import vague_code.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_resolve_api_key", lambda env: "sk-" + env)
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        a = app._begin_new_session()
        a.provider = "deepseek"
        a.model = "deepseek-v4-pro"
        a.agent.config.model = "deepseek-v4-pro"
        a.agent.backend = type("B", (), {"name": "ds"})()
        app._handle_slash("/new")
        b = app._begin_new_session()
        b.provider = "openai"
        b.model = "gpt-5.6-sol"
        b.agent.config.model = "gpt-5.6-sol"
        b.agent.backend = type("B", (), {"name": "oa"})()
        assert a.agent.config.model == "deepseek-v4-pro"
        assert b.agent.config.model == "gpt-5.6-sol"
        assert a.agent.backend is not b.agent.backend
        # 切到会话 A → topbar 显示 deepseek；切回 B → openai
        app._switch_session(a.run_id)
        await pilot.pause()
        assert "deepseek-v4-pro" in app._topbar_text()
        app._switch_session(b.run_id)
        await pilot.pause()
        assert "gpt-5.6-sol" in app._topbar_text()

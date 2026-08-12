from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import Screen
from textual.timer import Timer
from textual.worker import Worker, get_current_worker
from textual.widgets import Static, TextArea

from vague_code.agent.ir import (
    ArgsDelta,
    MessageEnd,
    MessageStart,
    RetryNotice,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolUseEnd,
    ToolUseStart,
)
from vague_code.agent.backend import ModelBackend
from vague_code.agent.permission import Decision, Operation
from vague_code.tui.commands import (
    CompositeCommandHandler,
    HelpCommandHandler,
    ModelCommandHandler,
    PermissionCommandHandler,
    SessionCommandHandler,
    picker_command,
)
from vague_code.tui.mixin import VagueCodeViewMixin, _TURN_START_ACTIVITY
from vague_code.tui.picker import TuiPickerItem, TuiPickerState, render_picker
from vague_code.tui.runner import VagueCodeAgentRunner
from vague_code.tui.session import SessionManager, SessionState
from vague_code.tui.state import TuiEntryKind, TuiTranscript
from vague_code.tui.views.activity import compact_tool_content
from vague_code.tui.views.topbar import topbar_markup
from vague_code.tui.views.welcome import welcome_renderable
from vague_code.tui.widgets import ComposerTextArea, VagueCodeScreen, _plain_static
from vague_code.tui.widgets.command_suggest import CommandSuggest
from vague_code.tui.widgets.conversation import ConversationView
from vague_code.tui.widgets.sidebar import SessionSidebar
from vague_code.tui.widgets.status import ActivityLine

PICKER_VISIBLE_LIMIT = 8


class VagueCodeApp(VagueCodeViewMixin, App):
    CSS_PATH = Path(__file__).parent / "theme.tcss"
    ALLOW_SELECT = True

    BINDINGS = [
        Binding("ctrl+c", "copy_or_interrupt", "Copy / interrupt", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Toggle sidebar"),
        Binding("t", "toggle_thinking", "Toggle thinking"),
        Binding("f1", "show_help", "Help", show=False),
    ]

    WELCOME_PARTICLE_INTERVAL_SECONDS = 0.85
    COMPACT_WELCOME_MAX_WIDTH = 80
    COMPACT_WELCOME_MAX_HEIGHT = 24
    ESC_INTERRUPT_WINDOW_SECONDS = 1.0

    def get_default_screen(self) -> Screen:
        return VagueCodeScreen(id="_default")

    def __init__(
        self,
        *,
        config,
        backend,
        task: str = "",
        workdir: str = ".",
        provider: str = "deepseek",
        file_config: dict | None = None,
        needs_setup: bool = False,
    ) -> None:
        super().__init__()
        self._config = config
        self._backend = backend
        self._provider = provider
        self._file_config = file_config or {}
        self._needs_setup = needs_setup
        self._agent_task = task
        self._workdir = workdir
        self._rules_path = Path(workdir) / ".agent" / "permission-rules.json"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._trajectory = None
        self._total_reclaimed = 0
        self._activity_text = "idle · ready"
        self._chat_busy = False
        self._chat_turn_token = 0
        self._turn_started_at = 0.0
        self._turn_tool_count = 0
        self._tool_entries = {}
        self._tool_names = {}
        self._tool_args_buffer = {}
        self._running_tool_call_ids = set()
        self._reset_stream_state()
        self._sessions = SessionManager()
        self._idle_transcript = TuiTranscript()
        self._turn_token_counter = 0
        self._session_workers: dict[str, Worker] = {}
        self._permission_queue: list = []
        self._permission_lock = threading.Lock()
        self._welcome_widget: Static | None = None
        self._welcome_particle_timer: Timer | None = None
        self._welcome_particle_frame = 0
        self._command_handler = CompositeCommandHandler(
            [
                HelpCommandHandler(self),
                SessionCommandHandler(self),
                ModelCommandHandler(self),
                PermissionCommandHandler(self),
            ]
        )
        self._picker: TuiPickerState | None = None
        self._input_history: list[str] = []
        self._input_history_index: int | None = None
        self._last_escape_at = 0.0
        self._guidance_lock = threading.Lock()
        self._reasoning_full: dict[int, str] = {}

    @property
    def transcript(self) -> TuiTranscript:
        state = self._sessions.current
        return state.transcript if state is not None else self._idle_transcript

    def _refresh_sidebar(self) -> None:
        sidebar = self._query_mounted("#sidebar")
        if sidebar is None or not hasattr(sidebar, "update_sessions"):
            return
        items = [
            (run_id, state.title, state.busy)
            for run_id, state in self._sessions.sessions.items()
        ]
        try:
            from vague_code.tui.session_lib import list_recent_runs
            for run in list_recent_runs(self._config.db_path, limit=20, mode="chat"):
                if run.run_id not in self._sessions.sessions:
                    items.append((run.run_id, run.title or run.task[:18], False))
        except Exception:
            pass
        sidebar.update_sessions(items, self._sessions.current_run_id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self._query_mounted("#sidebar")
        if sidebar is None:
            return
        if "hidden" in sidebar.classes:
            sidebar.remove_class("hidden")
            self._refresh_sidebar()
            sidebar.focus()
        else:
            sidebar.add_class("hidden")
            self.query_one("#input").focus()

    def _sidebar_visible(self) -> bool:
        sidebar = self._query_mounted("#sidebar")
        return sidebar is not None and "hidden" not in sidebar.classes

    def on_session_sidebar_session_selected(self, event: SessionSidebar.SessionSelected) -> None:
        event.stop()
        self._switch_session(event.run_id)

    def on_session_sidebar_session_delete_requested(self, event: SessionSidebar.SessionDeleteRequested) -> None:
        event.stop()
        self._prompt_delete_session(event.run_id)

    def _switch_session(self, run_id: str) -> None:
        output = self.query_one("#output", ConversationView)
        state = self._sessions.switch(run_id)
        if state is None:
            try:
                from vague_code.agent.trajectory import Trajectory
                traj = Trajectory.from_db(run_id, self._config.db_path)
            except Exception as e:
                self._write_line(f"加载会话失败: {e}", kind=TuiEntryKind.ERROR)
                return
            state = self._sessions.create(run_id, run_id)
            from vague_code.agent.trajectory import EventType
            is_chat = any(
                e.type == EventType.run_start and e.payload.get("mode") == "chat"
                for e in traj.events
            )
            if is_chat:
                # 历史 chat 会话：接线 agent，后续输入走 chat_resume 接续原 run
                state.agent = self._new_session_agent(state)
                state.resume_run_id = run_id
            state.transcript = TuiTranscript()
            output.transcript = state.transcript
            self._dismiss_welcome()
            self._replay_trajectory(traj)
        else:
            output.transcript = state.transcript
            output.render_transcript()
        self._reset_stream_state()
        self._set_activity("idle · ready")
        self._refresh_topbar()
        self._refresh_sidebar()
        self.query_one("#input").focus()

    def _prompt_delete_session(self, run_id: str | None = None) -> None:
        sidebar = self._query_mounted("#sidebar")
        if sidebar is None or not hasattr(sidebar, "select_current"):
            return
        if run_id is None:
            run_id = sidebar.select_current()
        if not run_id:
            return
        state = self._sessions.get(run_id)
        if state is not None and state.busy:
            self._write_line(
                f"会话 {run_id[:8]} 运行中，请先 Esc 中断再删除。",
                kind=TuiEntryKind.SYSTEM,
            )
            return
        title = state.title if state is not None else run_id
        self.run_worker(
            self._confirm_and_delete(run_id, title),
            name="confirm-delete",
            exit_on_error=False,
        )

    async def _confirm_and_delete(self, run_id: str, title: str) -> None:
        from vague_code.tui.screens.confirm import ConfirmDialog

        message = f"删除会话「{title}」？轨迹数据将从数据库永久删除，不可恢复。"
        try:
            confirmed = await self.push_screen_wait(ConfirmDialog("删除会话", message))
        finally:
            sidebar = self._query_mounted("#sidebar")
            if sidebar is not None and self._sidebar_visible():
                sidebar.focus()
        if confirmed:
            self._delete_session(run_id)

    def _delete_session(self, run_id: str) -> None:
        try:
            import sqlite3
            conn = sqlite3.connect(self._config.db_path, timeout=5)
            try:
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "events" in tables:
                    conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                if "runs" in tables:
                    conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self._write_line(f"删除会话失败: {e}", kind=TuiEntryKind.ERROR)
            return
        self._delete_session_artifacts(run_id)
        was_current = self._sessions.remove(run_id)
        self._refresh_sidebar()
        if was_current:
            self._after_current_session_deleted()
        else:
            self._write_line(f"已删除会话 {run_id[:8]}。", kind=TuiEntryKind.SYSTEM)
        sidebar = self._query_mounted("#sidebar")
        if sidebar is not None and self._sidebar_visible():
            sidebar.focus()

    def _delete_session_artifacts(self, run_id: str) -> None:
        """清理会话的磁盘产物：轨迹 jsonl 导出 + memory.db 中的会话记忆。"""
        from pathlib import Path

        for path in (
            Path(self._config.db_path).parent / f"{run_id}.jsonl",
            Path(self._config.db_path).parent / f"{run_id}.recovery.jsonl",
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            memory_db = Path(self._config.memory.memory_db_path)
            if memory_db.exists():
                import sqlite3 as _sq
                mconn = _sq.connect(str(memory_db), timeout=5)
                try:
                    tables = {
                        r[0]
                        for r in mconn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if "memories" in tables:
                        mconn.execute(
                            "DELETE FROM memories WHERE source_session=?", (run_id,)
                        )
                        mconn.commit()
                finally:
                    mconn.close()
        except Exception:
            pass

    def _after_current_session_deleted(self) -> None:
        next_state = self._sessions.current
        output = self.query_one("#output", ConversationView)
        if next_state is not None:
            output.transcript = next_state.transcript
            output.render_transcript()
            self._write_line("已删除当前会话，已切换到其他会话。", kind=TuiEntryKind.SYSTEM)
        else:
            self._dismiss_welcome()
            output.clear()
            self._show_welcome()
            self._write_line("已删除最后一个会话。", kind=TuiEntryKind.SYSTEM)
        self._reset_stream_state()
        self._set_activity("idle · ready")
        self._refresh_topbar()

    def compose(self) -> ComposeResult:
        yield Static(self._topbar_text(), id="topbar", classes="topbar")
        with Horizontal(id="app-body"):
            yield SessionSidebar(id="sidebar", classes="sidebar hidden")
            with Vertical(id="main"):
                yield ConversationView(id="output")
                yield ActivityLine("idle · ready", id="activity", classes="activity-line")
                with Vertical(id="composer", classes="composer"):
                    yield CommandSuggest(id="command-suggest")
                    yield ComposerTextArea(
                        placeholder="输入消息，Enter 发送，Shift+Enter 换行，Ctrl+C 退出",
                        id="input",
                        show_line_numbers=False,
                        soft_wrap=True,
                        compact=True,
                    )

    def on_mount(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.title = f"vague-code — {self._workdir}"
        self._show_welcome()
        self.query_one("#input").focus()
        if self._agent_task:
            self._submit_task(self._agent_task)
            self._agent_task = ""
        if self._needs_setup:
            self.call_after_refresh(self._open_setup_wizard)

    def _open_setup_wizard(
        self,
        preselect: str | None = None,
        preselect_model: str = "",
        cancellable: bool = False,
    ) -> None:
        from vague_code.tui.screens.setup import SetupWizard
        self.push_screen(SetupWizard(
            self, preselect=preselect, preselect_model=preselect_model, cancellable=cancellable,
        ))

    def _apply_setup(
        self,
        provider: str,
        base_url: str,
        key_env: str,
        protocol: str,
        model: str,
        key: str,
    ) -> None:
        """引导完成：写全局配置 + 重建 backend（ADR-0037）。"""
        from vague_code.config import (
            DEFAULT_MODELS,
            build_backend,
            global_config_dir,
            load_config,
            merge_provider_config,
            write_env_key,
        )

        cfg_dir = global_config_dir()
        write_env_key(cfg_dir / ".env", key_env, key)
        spec: dict = {"baseUrl": base_url, "apiKeyEnv": key_env}
        if protocol != "openai":
            spec["protocol"] = protocol
        merge_provider_config(
            cfg_dir / "vague-code.json", provider, spec,
            default_model=model or DEFAULT_MODELS.get(provider, ""),
        )
        self._config.model = model or DEFAULT_MODELS.get(provider, self._config.model)
        self._backend = build_backend(
            provider, key, base_url, protocol, self._config.transport.timeout_s,
        )
        self._provider = provider
        self._file_config = load_config(self._workdir)
        self._needs_setup = False
        # 会话级（ADR-0039）：配置完成后当前会话同步切换
        state = self._sessions.current
        if state is not None:
            state.provider = provider
            state.model = self._config.model
            state.backend = self._backend
            if state.agent is not None:
                state.agent.backend = self._backend
                state.agent.config.model = self._config.model
        self._refresh_topbar()
        self._write_line("配置完成，开始使用吧！", kind=TuiEntryKind.SYSTEM)

    def _on_terminal_resized(self) -> None:
        from textual.css.query import NoMatches
        try:
            self.query_one("#topbar")
        except NoMatches:
            return
        self._refresh_welcome_layout()
        self._refresh_topbar()

    def on_unmount(self) -> None:
        self._stop_welcome_particles()

    # ── Topbar / welcome ─────────────────────────────────────────────────────

    def _topbar_text(self) -> str:
        state = self._sessions.current
        provider = state.provider if state is not None and state.provider else self._provider
        model = state.model if state is not None and state.model else self._config.model
        mode = self._config.permission_mode
        cwd = Path(self._workdir).resolve().name or "."
        width = max(0, self.size.width - 4)
        return topbar_markup(
            self._activity_text, provider, model, mode, cwd, width,
            running_count=self._sessions.active_count(),
        )

    def _refresh_topbar(self) -> None:
        topbar = self._query_mounted("#topbar")
        if topbar is not None and hasattr(topbar, "update"):
            topbar.update(self._topbar_text())

    def _welcome_compact(self) -> bool:
        return self.size.width < self.COMPACT_WELCOME_MAX_WIDTH or self.size.height < self.COMPACT_WELCOME_MAX_HEIGHT

    def _show_welcome(self) -> None:
        if self._welcome_widget is not None:
            return
        output = self.query_one("#output")
        existing = output.query("Static#welcome")
        if existing:
            self._welcome_widget = existing[0]
            return
        widget = _plain_static("", id="welcome", classes="welcome")
        widget.update(welcome_renderable(compact=self._welcome_compact(), particle_frame=0))
        self._welcome_widget = widget
        output.mount(widget)
        output.add_class("welcome-active")
        self._welcome_particle_frame = 0
        self._start_welcome_particles()

    def _start_welcome_particles(self) -> None:
        self._stop_welcome_particles()
        self._welcome_particle_timer = self.set_interval(
            self.WELCOME_PARTICLE_INTERVAL_SECONDS, self._advance_welcome_particles
        )

    def _stop_welcome_particles(self) -> None:
        if self._welcome_particle_timer is not None:
            self._welcome_particle_timer.stop()
            self._welcome_particle_timer = None

    def _advance_welcome_particles(self) -> None:
        if self._welcome_widget is None:
            return
        self._welcome_particle_frame += 1
        self._welcome_widget.update(
            welcome_renderable(
                compact=self._welcome_compact(), particle_frame=self._welcome_particle_frame
            )
        )

    def _refresh_welcome_layout(self) -> None:
        if self._welcome_widget is None:
            return
        self._welcome_widget.update(
            welcome_renderable(
                compact=self._welcome_compact(), particle_frame=self._welcome_particle_frame
            )
        )

    def _dismiss_welcome(self) -> None:
        if self._welcome_widget is None:
            return
        self._stop_welcome_particles()
        welcome, self._welcome_widget = self._welcome_widget, None
        welcome.remove()
        output = self.query_one("#output")
        output.remove_class("welcome-active")

    # ── Composer ─────────────────────────────────────────────────────────────

    async def on_composer_text_area_submitted(self, event: ComposerTextArea.Submitted) -> None:
        event.stop()
        if self._suggest_enter():
            return
        if self._picker is not None:
            input_widget = self.query_one("#input", ComposerTextArea)
            text = input_widget.text.strip()
            if text.isdigit():
                if self._picker_select_number(int(text)):
                    input_widget.clear()
                    return
            self._picker_select_index(self._picker.selected_index)
            input_widget.clear()
            return
        await self._submit_composer()

    async def _submit_composer(self) -> None:
        input_widget = self.query_one("#input", ComposerTextArea)
        text = input_widget.text.strip()
        input_widget.clear()
        if not text:
            return
        self._dismiss_welcome()
        self._record_input_history(text)
        self._submit_task(text)

    def _submit_task(self, text: str) -> None:
        if text.strip().lower() == "exit":
            self.exit("User quit")
            return

        if self._picker is not None and text.isdigit():
            if self._picker_select_number(int(text)):
                return

        if text.startswith("/"):
            self._handle_slash(text)
            return

        self._submit_chat(text)

    def _submit_chat(self, text: str) -> None:
        state = self._sessions.current
        if state is None:
            state = self._begin_new_session()
        if state.agent is None:
            state.agent = self._new_session_agent(state)
        self._dismiss_welcome()
        user_entry = state.transcript.add(TuiEntryKind.USER, text)
        output = self.query_one("#output", ConversationView)
        output.add_entry(user_entry)
        if state.busy or self._session_worker_running(state):
            self._add_guidance(state, text)
            self._write_line("已加入运行队列，将在下一轮生效。", kind=TuiEntryKind.SYSTEM)
            return
        token = self._begin_session_turn(state)
        self.run_worker(
            lambda: self._run_agent_worker(state, text, token),
            thread=True,
            exclusive=False,
            name=f"agent-{state.run_id}",
            group="agent",
        )

    def _session_worker_running(self, state: SessionState) -> bool:
        """True when a worker thread for this session may still be alive.

        中断（cancel）后 worker.state 立即变为 CANCELLED，但线程可能仍在
        工具执行中（finally 未执行、map 条目未 pop）——此时也视为忙碌，
        避免同会话启动第二个 worker 与旧生成器并发改 _chat_messages。
        """
        worker = self._session_workers.get(state.run_id)
        if worker is None:
            return False
        name = getattr(worker.state, "name", "")
        if name == "CANCELLED":
            return True
        return name == "RUNNING"

    def _start_queued_turn(self, state: SessionState, text: str) -> None:
        """（UI 线程）旧 worker 退出后自动开始排队的下一轮。"""
        if state.busy or self._session_worker_running(state):
            self._add_guidance(state, text)
            return
        if self._sessions.current is not state:
            self._add_guidance(state, text)
            return
        token = self._begin_session_turn(state)
        self.run_worker(
            lambda: self._run_agent_worker(state, text, token),
            thread=True,
            exclusive=False,
            name=f"agent-{state.run_id}",
            group="agent",
        )

    def _begin_new_session(self) -> SessionState:
        from uuid import uuid4

        placeholder = f"new_{uuid4().hex[:8]}"
        state = self._sessions.create(placeholder, "新会话")
        state.agent = self._new_session_agent(state)
        output = self.query_one("#output", ConversationView)
        output.transcript = state.transcript
        self._dismiss_welcome()
        output.clear()
        self._show_welcome()
        self._refresh_sidebar()
        return state

    def _new_session_agent(self, state: SessionState):
        from vague_code.agent.loop import Agent

        import copy
        provider = state.provider or self._provider
        model = state.model or self._config.model
        backend = state.backend
        if backend is None:
            backend = self._session_backend(provider)
            if backend is None:
                raise RuntimeError(f"Provider '{provider}' 未配置 API key")
            state.backend = backend
            state.provider = provider
            state.model = model
        config = copy.copy(self._config)
        config.model = model
        agent = Agent(config, backend)
        agent._on_permission = lambda op, decision, _s=state: self._thread_permission(op, decision, _s)
        agent.on_tool_result = lambda tid, name, content, err, _s=state: self.call_from_thread(
            self._on_tool_result, tid, name, content, err, _s, _s.active_token
        )
        agent.on_state_change = lambda kind, payload, _s=state: self.call_from_thread(
            self._on_state_change, kind, payload, _s, _s.active_token
        )
        agent.guidance_provider = lambda _s=state: self._drain_guidance(_s)
        for rule in self._load_permission_rules():
            agent.add_permission_rule(rule["pattern"], rule.get("action", "allow"))
        return agent

    def _session_backend(self, provider: str) -> ModelBackend | None:
        """按 provider 解析 key/base_url/protocol 构造后端（ADR-0039 会话级）。

        与 app 默认 provider 相同时直接复用启动时的后端；key 缺失返回 None。
        """
        from vague_code.config import build_backend

        if provider == self._provider and self._backend is not None:
            return self._backend
        from vague_code.cli import _provider_settings
        base_url, key_env, protocol, user_agent = _provider_settings(provider, None, None, self._file_config)
        key = self._resolve_key_for(provider)
        if not key:
            return None
        return build_backend(provider, key, base_url, protocol, self._config.transport.timeout_s, user_agent=user_agent)

    def _resolve_key_for(self, provider: str) -> str | None:
        """provider 的 API key：项目 .env → 全局 .env → 环境变量（ADR-0039）。"""
        from vague_code.cli import _provider_settings, _resolve_api_key
        _base_url, key_env, _protocol, _user_agent = _provider_settings(provider, None, None, self._file_config)
        if not key_env:
            return None
        return _resolve_api_key(key_env)

    def _begin_session_turn(self, state: SessionState) -> int:
        self._turn_token_counter += 1
        token = self._turn_token_counter
        state.active_token = token
        state.busy = True
        self._reset_stream_state()
        self._start_turn_metrics()
        self._tool_entries = {}
        self._tool_names = {}
        self._tool_args_buffer = {}
        self._set_activity(_TURN_START_ACTIVITY)
        return token

    # ── Slash commands ───────────────────────────────────────────────────────

    def _handle_slash(self, text: str) -> None:
        result = self._command_handler.handle(text)
        if result.handled:
            if result.output:
                self._write_line(result.output, kind=TuiEntryKind.COMMAND)
            self._handle_command_action(result.action, output=result.output)
            return
        self._write_line(f"Unknown command: {text}", kind=TuiEntryKind.ERROR)

    def _handle_command_action(self, action: dict | None, *, output: str = "") -> bool:
        if not action:
            return False
        action_type = action.get("type")
        if action_type == "open_picker":
            self._open_picker(
                kind=str(action.get("kind") or "resume"),
                title=str(action.get("title") or "Select:"),
                items=[
                    TuiPickerItem(id=str(item.get("id") or ""), label=str(item.get("label") or "?"), detail=str(item.get("detail") or ""))
                    for item in action.get("items", [])
                    if isinstance(item, dict)
                ],
                empty_text="No items.",
            )
            return True
        if action_type == "new_session":
            self._picker = None
            state = self._begin_new_session()
            if output:
                state.transcript.add(TuiEntryKind.COMMAND, output)
            return True
        if action_type == "clear_output":
            self._picker = None
            self._clear_output()
            return True
        if action_type == "resume_session":
            run_id = str(action.get("run_id") or "")
            if run_id:
                self._start_resume(run_id)
            return True
        if action_type == "compact_session":
            self._start_compact()
            return True
        if action_type == "model_changed":
            return self._apply_model_change(
                str(action.get("model") or ""), str(action.get("provider") or ""),
            )
        return False

    def _apply_model_change(self, model: str, provider: str) -> bool:
        """会话级模型切换（ADR-0039）。

        同 provider 直切模型；跨 provider 时目标有 key 则换会话 backend 直切，
        无 key 弹 SetupWizard（预选目标 provider/模型，可取消 → 零改动回退）。
        """
        if not model:
            return False
        state = self._sessions.current
        if state is None:
            # 无会话（欢迎页）：作用于 app 默认
            self._config.model = model
            if provider:
                self._provider = provider
            self._refresh_topbar()
            return True
        target_provider = provider or state.provider or self._provider
        if target_provider == (state.provider or self._provider):
            # 同 provider：仅换模型
            state.model = model
            if state.agent is not None:
                state.agent.config.model = model
            self._refresh_topbar()
            return True
        # 跨 provider：目标 key 缺失 → 引导配置（取消则回退原模型）
        if not self._resolve_key_for(target_provider):
            self._open_setup_wizard(
                preselect=target_provider, preselect_model=model, cancellable=True,
            )
            return True
        key = self._resolve_key_for(target_provider)
        self._switch_session_provider(state, target_provider, model, key or "")
        return True

    def _switch_session_provider(
        self, state: SessionState, provider: str, model: str, key: str,
    ) -> None:
        """会话级换 provider：重建 backend + 换 agent.backend + 更新状态。"""
        from vague_code.config import build_backend
        from vague_code.cli import _provider_settings

        base_url, key_env, protocol, user_agent = _provider_settings(provider, None, None, self._file_config)
        new_backend = build_backend(
            provider, key, base_url, protocol, self._config.transport.timeout_s, user_agent=user_agent,
        )
        state.backend = new_backend
        state.provider = provider
        state.model = model
        if state.agent is not None:
            state.agent.backend = new_backend
            state.agent.config.model = model
        self._refresh_topbar()
        self._write_line(
            f"已切换会话模型：{provider}/{model}", kind=TuiEntryKind.SYSTEM,
        )

    def _clear_output(self) -> None:
        state = self._sessions.current
        output = self.query_one("#output", ConversationView)
        if state is not None:
            state.transcript = TuiTranscript()
            output.transcript = state.transcript
        output.clear()

    # ── Picker ───────────────────────────────────────────────────────────────

    def _open_picker(self, **fields) -> None:
        self._picker = TuiPickerState(**fields)
        self._render_picker()

    def _render_picker(self) -> None:
        picker = self._picker
        if picker is None:
            return
        self._replace_last_command_output(
            render_picker(picker, limit=PICKER_VISIBLE_LIMIT)
        )

    def _replace_last_command_output(self, text: str) -> None:
        for entry in reversed(self.transcript.entries):
            if entry.kind == TuiEntryKind.COMMAND:
                entry.body = text
                widget = entry.widget
                if widget is not None and hasattr(widget, "update"):
                    widget.update(text)
                return
        self._write_line(text, kind=TuiEntryKind.COMMAND)

    def _picker_select_number(self, number: int) -> bool:
        picker = self._picker
        if picker is None:
            return False
        index = number - 1
        if index < 0 or index >= len(picker.items):
            self._write_line("Invalid selection.", kind=TuiEntryKind.ERROR)
            return True
        self._picker_select_index(index)
        return True

    def _picker_select_index(self, index: int) -> None:
        picker = self._picker
        if picker is None:
            return
        if index < 0 or index >= len(picker.items):
            return
        item = picker.items[index]
        command = picker_command(picker.kind, item)
        self._picker = None
        if not command:
            return
        self._handle_slash(command)

    def _handle_picker_key(self, event: Key) -> bool:
        picker = self._picker
        if picker is None:
            return False
        if event.key == "up":
            picker.move(-1)
            self._render_picker()
            return True
        if event.key == "down":
            picker.move(1)
            self._render_picker()
            return True
        if event.key == "enter":
            self._picker_select_index(picker.selected_index)
            return True
        if event.key == "escape":
            self._picker = None
            self._write_line("Picker cancelled.", kind=TuiEntryKind.COMMAND)
            return True
        return False

    # ── Command suggest（ADR-0038）────────────────────────────────────────────

    def on_text_area_changed(self, event) -> None:
        """输入变化 → 更新 / 命令候选浮层。"""
        if getattr(event.text_area, "id", None) is not self.query_one("#input", ComposerTextArea).id:
            return
        suggest = self.query_one("#command-suggest", CommandSuggest)
        suggest.show_for(self.query_one("#input").text)

    def on_focus(self, event) -> None:
        """焦点切走输入框 → 收起命令浮层（否则永远覆盖输入栏）。"""
        if getattr(event, "control", None) is None:
            return
        if getattr(event.control, "id", None) != "input":
            suggest = self.query_one("#command-suggest", CommandSuggest)
            if suggest.is_visible():
                suggest.show_for("")

    def on_composer_text_area_blurred(self, event) -> None:
        """输入框失焦 → 收起命令浮层（Focus 不冒泡，widget 主动上报）。"""
        event.stop()
        suggest = self.query_one("#command-suggest", CommandSuggest)
        if suggest.is_visible():
            suggest.show_for("")

    def on_composer_text_area_focused(self, event) -> None:
        """输入框重新聚焦 → 按当前文本恢复命令浮层。"""
        event.stop()
        suggest = self.query_one("#command-suggest", CommandSuggest)
        suggest.show_for(self.query_one("#input").text)

    def _handle_suggest_key(self, event: Key) -> bool:
        """浮层显示时拦截 ↑/↓/Esc（Enter 由 Submitted 处理器接管，ADR-0038）。"""
        if getattr(self.focused, "id", None) != "input":
            return False
        suggest = self.query_one("#command-suggest", CommandSuggest)
        if not suggest.is_visible():
            return False
        if event.key in ("up", "down"):
            suggest.move(1 if event.key == "down" else -1)
            return True
        if event.key == "escape":
            suggest.show_for("")
            return True
        return False

    def _suggest_enter(self) -> bool:
        """浮层可见时 Enter：无参命令直接执行，有参命令填入命令+空格。

        浮层可见即输入为命令前缀（filter_commands 含空格即收起），
        选中项即用户意图——无论文本是否已完整输入，Enter 一律按选中项处理。
        """
        suggest = self.query_one("#command-suggest", CommandSuggest)
        if not suggest.is_visible():
            return False
        item = suggest.selected()
        if item is None:
            suggest.show_for("")
            return False
        cmd, _desc, needs_args = item
        input_widget = self.query_one("#input")
        if needs_args:
            # 有参命令：填入"命令+空格"继续输参数
            input_widget.load_text(cmd + " ")
            input_widget.cursor_location = input_widget.document.end
            suggest.show_for(cmd + " ")
        else:
            # 无参命令：直接执行
            input_widget.load_text("")
            suggest.show_for("")
            self._handle_slash(cmd)
        return True

    # ── Input history ─────────────────────────────────────────────────────────

    def _record_input_history(self, text: str) -> None:
        if not self._input_history or self._input_history[-1] != text:
            self._input_history.append(text)
        self._input_history_index = None

    def _recall_input_history(self, direction: str) -> str | None:
        if not self._input_history:
            return None
        if direction == "up":
            if self._input_history_index is None:
                self._input_history_index = len(self._input_history) - 1
            else:
                self._input_history_index = max(0, self._input_history_index - 1)
            return self._input_history[self._input_history_index]
        if direction == "down":
            if self._input_history_index is None:
                return None
            if self._input_history_index >= len(self._input_history) - 1:
                self._input_history_index = None
                return ""
            self._input_history_index += 1
            return self._input_history[self._input_history_index]
        return None

    def on_key(self, event: Key) -> None:
        if self._sidebar_visible() and self.focused is not None and self.focused.id == "sidebar":
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                self.action_toggle_sidebar()
                return
            if event.key == "n":
                event.stop()
                event.prevent_default()
                self._handle_slash("/new")
                return
            if event.key in ("up", "down"):
                event.stop()
                event.prevent_default()
                sidebar = self.query_one("#sidebar", SessionSidebar)
                sidebar.move_selection(1 if event.key == "down" else -1)
                return
            if event.key == "d":
                event.stop()
                event.prevent_default()
                self._prompt_delete_session()
                return
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                sidebar = self.query_one("#sidebar", SessionSidebar)
                run_id = sidebar.select_current()
                if run_id is not None:
                    self._switch_session(run_id)
                return
        if self._picker is not None and self._handle_picker_key(event):
            event.stop()
            event.prevent_default()
            return
        if self._handle_suggest_key(event):
            event.stop()
            event.prevent_default()
            return
        if event.key == "escape":
            if self._handle_escape_interrupt():
                event.stop()
                event.prevent_default()
            return
        if event.key not in {"up", "down"}:
            return
        focused = getattr(self, "focused", None)
        if getattr(focused, "id", None) != "input":
            return
        input_widget = self.query_one("#input", ComposerTextArea)
        recalled = self._recall_input_history(event.key)
        if recalled is None:
            return
        event.stop()
        event.prevent_default()
        input_widget.load_text(recalled)
        input_widget.cursor_location = input_widget.document.end

    def _handle_escape_interrupt(self) -> bool:
        state = self._sessions.current
        if state is None or not state.busy:
            self._last_escape_at = 0.0
            input_widget = self._query_mounted("#input")
            if input_widget is not None and hasattr(input_widget, "focus"):
                input_widget.focus()
            return False
        now = time.monotonic()
        if now - self._last_escape_at > self.ESC_INTERRUPT_WINDOW_SECONDS:
            self._last_escape_at = now
            self._set_activity("running · press Esc again to interrupt")
            return True
        self._last_escape_at = 0.0
        self._interrupt_chat_turn()
        return True

    def _interrupt_chat_turn(self) -> None:
        state = self._sessions.current
        if state is not None:
            state.busy = False
            state.active_token = None
        worker = self._session_workers.get(state.run_id) if state else None
        if worker is not None and getattr(worker.state, "name", "") == "RUNNING":
            worker.cancel()
        self._reset_stream_state()
        self._finish_turn_metrics()
        self._set_activity("interrupted")
        self._write_line("已中断当前回合。", kind=TuiEntryKind.SYSTEM)
        self._refresh_sidebar()

    def _start_resume(self, run_id: str) -> None:
        try:
            from vague_code.agent.trajectory import Trajectory
            traj = Trajectory.from_db(run_id, self._config.db_path)
        except Exception as e:
            self._write_line(f"Resume failed: {e}", kind=TuiEntryKind.ERROR)
            return
        self._picker = None
        from vague_code.agent.trajectory import EventType as TrajEventType
        is_chat = any(
            e.type == TrajEventType.run_start
            and e.payload.get("mode") == "chat"
            for e in traj.events
        )
        if is_chat:
            self._resume_chat_session(run_id, traj)
            return
        if self._sessions.current is not None and self._sessions.current.busy:
            self._write_line("Agent 正在运行，请先等待或中断。", kind=TuiEntryKind.SYSTEM)
            return
        self._clear_output()
        self._replay_trajectory(traj)
        token = self._begin_chat_turn(f"resume {run_id}")
        self._write_line(f"正在恢复会话 {run_id}…", kind=TuiEntryKind.COMMAND)
        self.run_worker(
            lambda: self._run_resume_worker(run_id, traj, token),
            thread=True,
            exclusive=False,
            name="agent-task-resume",
            group="agent",
        )

    def _resume_chat_session(self, run_id: str, traj) -> None:
        """Load a chat session from DB into the session manager (parallel-friendly).

        The session is recreated with a fresh Agent wired to per-session callbacks;
        the transcript is replayed and the manager switches to it.
        """
        state = self._sessions.create(run_id, run_id)
        state.agent = self._new_session_agent(state)
        state.transcript = TuiTranscript()
        output = self.query_one("#output", ConversationView)
        output.transcript = state.transcript
        self._dismiss_welcome()
        self._replay_trajectory(traj)
        self._write_line(f"正在恢复会话 {run_id}…", kind=TuiEntryKind.COMMAND)
        token = self._begin_session_turn(state)
        self.run_worker(
            lambda: self._run_resume_worker(run_id, traj, token),
            thread=True,
            exclusive=False,
            name=f"agent-{run_id}",
            group="agent",
        )

    def _start_compact(self) -> None:
        """手动压缩当前会话上下文（/compact）：stale_snip + LLM 摘要，绕过阈值。"""
        state = self._sessions.current
        if state is None or state.agent is None:
            self._write_line("当前没有活动会话。", kind=TuiEntryKind.SYSTEM)
            return
        if state.busy:
            self._write_line(
                "会话运行中，请先等待或 Esc 中断后再压缩。",
                kind=TuiEntryKind.SYSTEM,
            )
            return
        self._write_line("正在压缩上下文…", kind=TuiEntryKind.SYSTEM)
        self.run_worker(
            lambda: self._run_compact_worker(state),
            thread=True,
            exclusive=False,
            name=f"compact-{state.run_id}",
            group="agent",
        )

    def _run_compact_worker(self, state: SessionState) -> None:
        try:
            agent = state.agent
            if agent is None:
                self.call_from_thread(
                    self._write_line, "会话 agent 未初始化。", kind=TuiEntryKind.ERROR
                )
                return
            result = agent.compact_chat()
            saved = result.get("before", 0) - result.get("after", 0)
            if saved > 0:
                self.call_from_thread(
                    self._write_line,
                    f"已压缩：回收 {saved / 1024:.1f}k tokens。",
                    kind=TuiEntryKind.SYSTEM,
                )
            else:
                self.call_from_thread(
                    self._write_line,
                    "上下文已是最新，无需压缩。",
                    kind=TuiEntryKind.SYSTEM,
                )
            # 对齐 opencode：压缩完成后把摘要展示在对话流中（ADR-0036）
            summary = str(result.get("summary") or "").strip()
            if summary:
                self.call_from_thread(
                    self._show_compact_summary, state, summary,
                )
        except Exception as exc:
            self.call_from_thread(
                self._write_line,
                f"压缩失败: {type(exc).__name__}: {exc}",
                kind=TuiEntryKind.ERROR,
            )

    def _show_compact_summary(self, state: SessionState, summary: str) -> None:
        """把压缩摘要展示在对话流中（对齐 opencode 的摘要消息，ADR-0036）。"""
        if self._sessions.current is not state:
            return
        self._write_markdown_message(f"[会话摘要]\n{summary}")

    def _replay_trajectory(self, traj) -> None:
        from vague_code.agent.trajectory import EventType
        from vague_code.tui.state import TuiTranscriptEntry
        tool_entries: dict[str, TuiTranscriptEntry] = {}
        for ev in traj.events:
            if ev.type == EventType.run_start:
                task = str(ev.payload.get("task") or "").strip()
                if task:
                    entry = self.transcript.add(TuiEntryKind.USER, task)
                    output = self.query_one("#output", ConversationView)
                    output.add_entry(entry)
            elif ev.type == EventType.llm_response:
                for block in ev.payload.get("blocks", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        self._write_markdown_message(str(block["text"]))
                    elif block.get("type") == "tool_use":
                        name = str(block.get("name") or "tool")
                        entry = self._write_line(
                            f"正在调用工具：{name}",
                            kind=TuiEntryKind.TOOL,
                            label=f"tool {name} running",
                            status="running",
                        )
                        tool_entries[str(block.get("id") or "")] = entry
            elif ev.type == EventType.tool_result:
                tool_id = str(ev.payload.get("tool_use_id") or "")
                tool_entry = tool_entries.get(tool_id)
                if tool_id in tool_entries:
                    tool_entries.pop(tool_id)
                ok = not ev.payload.get("is_error", False)
                summary = compact_tool_content(str(ev.payload.get("content") or ""))
                status = "success" if ok else "error"
                suffix = f"：{summary}" if summary else ""
                text = f"工具{'完成' if ok else '失败'}{suffix}"
                if tool_entry is not None:
                    parts = tool_entry.label.split()
                    name = parts[1] if len(parts) > 1 else "tool"
                    tool_entry.body = text
                    tool_entry.status = status
                    tool_entry.label = f"tool {name} {status}"
                    output = self.query_one("#output", ConversationView)
                    output.update_entry(tool_entry)

    def _run_agent_worker(self, state: SessionState, text: str, token: int) -> None:
        worker = get_current_worker()
        self._session_workers[state.run_id] = worker
        try:
            agent = state.agent
            if agent is None:
                self.call_from_thread(self._on_agent_error, "会话 agent 未初始化", state, token)
                return
            if state.resume_run_id and not agent.in_chat:
                handle = agent.chat_resume(state.resume_run_id)
                state.resume_run_id = None
            else:
                handle = agent.chat(text, self._workdir)
            for ev in handle:
                if worker.is_cancelled:
                    handle.close()
                    return
                self.call_from_thread(self._on_stream_event, ev, state, token)
            self.call_from_thread(self._on_run_complete, handle.trajectory, state, token)
        except Exception as exc:
            self.call_from_thread(self._on_agent_error, f"{type(exc).__name__}: {exc}", state, token)
        finally:
            self._session_workers.pop(state.run_id, None)
            pending = self._drain_guidance(state)
            if pending and not self._session_worker_running(state):
                self.call_from_thread(self._start_queued_turn, state, "\n".join(pending))

    def _run_resume_worker(self, run_id: str, traj, token: int) -> None:
        worker = get_current_worker()
        if worker.is_cancelled:
            return
        try:
            from vague_code.agent.trajectory import EventType as TrajEventType
            is_chat = any(
                e.type == TrajEventType.run_start
                and e.payload.get("mode") == "chat"
                for e in traj.events
            )
            if is_chat:
                state = self._sessions.current
                if state is None or state.agent is None:
                    self._write_line("没有活动会话可恢复。", kind=TuiEntryKind.ERROR)
                    return
                self._session_workers[state.run_id] = worker
                try:
                    handle = state.agent.chat_resume(run_id)
                    for ev in handle:
                        if worker.is_cancelled:
                            handle.close()
                            return
                        self.call_from_thread(self._on_stream_event, ev, state, token)
                    self.call_from_thread(self._on_run_complete, handle.trajectory, state, token)
                finally:
                    self._session_workers.pop(state.run_id, None)
            else:
                runner = VagueCodeAgentRunner(
                    config=self._config,
                    backend=self._backend,
                    permission_rules=self._load_permission_rules(),
                    on_stream_event=lambda ev: self.call_from_thread(
                        self._on_stream_event, ev, None, token
                    ),
                    on_tool_result=lambda tid, name, content, err: self.call_from_thread(
                        self._on_tool_result, tid, name, content, err, None, token
                    ),
                    on_state_change=lambda kind, payload: self.call_from_thread(
                        self._on_state_change, kind, payload, None, token
                    ),
                    on_permission=self._thread_permission,
                    on_run_complete=lambda result: self.call_from_thread(
                        self._on_run_complete, result, None, token
                    ),
                    on_error=lambda msg: self.call_from_thread(self._on_agent_error, msg, None, token),
                    is_cancelled=lambda: worker.is_cancelled,
                    guidance_provider=lambda: [],
                )
                runner.resume(traj)
        except Exception as e:
            self.call_from_thread(self._on_agent_error, f"Resume failed: {e}", None, token)

    # ── Guidance queue ────────────────────────────────────────────────────────

    def _add_guidance(self, state: SessionState, text: str) -> None:
        with self._guidance_lock:
            state.pending_guidance.append(text)

    def _drain_guidance(self, state: SessionState) -> list[str]:
        with self._guidance_lock:
            guidance = list(state.pending_guidance)
            state.pending_guidance.clear()
        return guidance

    # ── Event dispatch (UI thread) ───────────────────────────────────────────

    def _on_stream_event(self, ev: StreamEvent, state: SessionState | None, token: int) -> None:
        if state is None:
            if not self._is_current_chat_turn(token):
                return
        elif state.active_token != token:
            return
        if state is not None and self._sessions.current is not state:
            self._apply_offline_event(state, ev)
            return
        if isinstance(ev, MessageStart):
            self._show_activity_animation("running", "llm responding")
        elif isinstance(ev, ThinkingStart):
            self._write_line("", kind=TuiEntryKind.REASONING, status="running")
        elif isinstance(ev, ThinkingDelta):
            self._append_reasoning(ev.delta)
            self._append_reasoning_text(ev.delta)
        elif isinstance(ev, ThinkingEnd):
            self._finalize_reasoning()
        elif isinstance(ev, TextDelta):
            if not self._stream_text_started:
                self._stream_text_started = True
                self._complete_working_indicator()
            self._append_stream_text(ev.delta)
        elif isinstance(ev, ToolUseStart):
            self._close_stream_segment_for_tool()
            self._tool_entries[ev.id] = self._write_line(
                f"正在调用工具：{ev.name}",
                kind=TuiEntryKind.TOOL,
                label=f"tool {ev.name} running",
                status="running",
            )
            self._tool_names[ev.id] = ev.name
            self._tool_args_buffer[ev.id] = ""
            self._running_tool_call_ids.add(ev.id)
            self._turn_tool_count = len(self._running_tool_call_ids)
            self._record_tool_activity(ev.name, "running")
        elif isinstance(ev, ArgsDelta):
            self._append_tool_args(ev.id, ev.delta)
        elif isinstance(ev, ToolUseEnd):
            pass
        elif isinstance(ev, RetryNotice):
            self._write_line(
                f"retry {ev.attempt}: {ev.reason}（{ev.delay_s:.1f}s 后重试）",
                kind=TuiEntryKind.SYSTEM,
            )
        elif isinstance(ev, MessageEnd):
            self._finalize_stream_widget()
            self._set_activity("running")

    def _apply_offline_event(self, state: SessionState, ev: StreamEvent) -> None:
        """Route an event of a non-current session: update its transcript only."""
        if isinstance(ev, TextDelta):
            entries = state.transcript.entries
            if entries and entries[-1].kind == TuiEntryKind.ASSISTANT:
                entries[-1].body += ev.delta
            else:
                state.transcript.add(TuiEntryKind.ASSISTANT, ev.delta)
        elif isinstance(ev, ThinkingDelta):
            entries = state.transcript.entries
            if entries and entries[-1].kind == TuiEntryKind.REASONING:
                entries[-1].body += ev.delta
            else:
                state.transcript.add(TuiEntryKind.REASONING, ev.delta, status="running")
        elif isinstance(ev, ThinkingEnd):
            entries = state.transcript.entries
            if entries and entries[-1].kind == TuiEntryKind.REASONING:
                entries[-1].status = None
        elif isinstance(ev, ToolUseStart):
            entry = state.transcript.add(
                TuiEntryKind.TOOL,
                f"正在调用工具：{ev.name}",
                label=f"tool {ev.name} running",
                status="running",
            )
            state.offline_tools[ev.id] = entry
        elif isinstance(ev, ArgsDelta):
            offline_entry = state.offline_tools.get(ev.id)
            if offline_entry is not None:
                offline_entry.body = f"正在调用工具：{offline_entry.label.split()[1]} {ev.delta[:80]}"
        elif isinstance(ev, RetryNotice):
            state.transcript.add(
                TuiEntryKind.SYSTEM,
                f"retry {ev.attempt}: {ev.reason}（{ev.delay_s:.1f}s 后重试）",
            )

    def _append_reasoning(self, delta: str) -> None:
        if not self.transcript.entries or self.transcript.entries[-1].kind != TuiEntryKind.REASONING:
            self._write_line("", kind=TuiEntryKind.REASONING, status="running")
        entry = self.transcript.entries[-1]
        entry.body += delta
        output = self.query_one("#output", ConversationView)
        output.update_entry(entry)

    def _finalize_reasoning(self) -> None:
        if not self.transcript.entries or self.transcript.entries[-1].kind != TuiEntryKind.REASONING:
            return
        entry = self.transcript.entries[-1]
        body = entry.body.strip()
        if not body:
            self.transcript.entries.pop()
            if entry.widget is not None:
                remove = getattr(entry.widget, "remove", None)
                if remove is not None:
                    remove()
            return
        entry.body = body
        if len(body) > 200:
            self._reasoning_full[entry.id] = body
            entry.body = self._reasoning_summary(entry)
            entry.status = "folded"
        else:
            entry.status = None
        output = self.query_one("#output", ConversationView)
        output.update_entry(entry)

    def _reasoning_summary(self, entry) -> str:
        full = self._reasoning_full.get(entry.id, entry.body)
        return f"[thinking — {len(full.split())} 词，按 T 展开]"

    def action_toggle_thinking(self) -> None:
        for entry in reversed(self.transcript.entries):
            if entry.kind == TuiEntryKind.REASONING and entry.id in self._reasoning_full:
                if entry.status == "folded":
                    entry.body = self._reasoning_full[entry.id]
                    entry.status = None
                else:
                    entry.body = self._reasoning_summary(entry)
                    entry.status = "folded"
                output = self.query_one("#output", ConversationView)
                output.update_entry(entry)
                return

    def action_show_help(self) -> None:
        from vague_code.tui.commands.handlers import _HELP_TEXT
        self._write_line(_HELP_TEXT, kind=TuiEntryKind.COMMAND)

    def _append_tool_args(self, tool_id: str, delta: str) -> None:
        entry = self._tool_entries.get(tool_id)
        if entry is None:
            return
        self._tool_args_buffer[tool_id] = self._tool_args_buffer.get(tool_id, "") + delta
        preview = compact_tool_content(self._tool_args_buffer[tool_id])[:120]
        tool_name = self._tool_names.get(tool_id, "tool")
        entry.body = f"正在调用工具：{tool_name} {preview}"
        output = self.query_one("#output", ConversationView)
        output.update_entry(entry)

    def _on_tool_result(
        self, tool_id: str, tool_name: str, content: str, is_error: bool,
        state: SessionState | None, token: int,
    ) -> None:
        if state is None:
            if not self._is_current_chat_turn(token):
                return
        elif state.active_token != token:
            return
        status = "error" if is_error else "success"
        summary = compact_tool_content(content)
        suffix = f"：{summary}" if summary else ""
        text = f"工具{'失败' if is_error else '完成'}：{tool_name}{suffix}"
        if state is not None and self._sessions.current is not state:
            entry = state.offline_tools.pop(tool_id, None)
            if entry is not None:
                entry.body = text
                entry.status = status
                entry.label = f"tool {tool_name} {status}"
            else:
                state.transcript.add(
                    TuiEntryKind.TOOL, text,
                    label=f"tool {tool_name} {status}", status=status,
                )
            return
        self._running_tool_call_ids.discard(tool_id)
        self._turn_tool_count = len(self._running_tool_call_ids) or self._turn_tool_count
        entry = self._tool_entries.pop(tool_id, None)
        if entry is not None:
            entry.body = text
            entry.status = status
            entry.label = f"tool {tool_name} {status}"
            output = self.query_one("#output", ConversationView)
            output.update_entry(entry)
        else:
            self._write_line(
                text,
                kind=TuiEntryKind.TOOL,
                label=f"tool {tool_name} {status}",
                status=status,
            )
        self._record_tool_activity(tool_name, status, summary)

    def _on_state_change(
        self, kind: str, payload: dict, state: SessionState | None, token: int,
    ) -> None:
        if state is None:
            if not self._is_current_chat_turn(token):
                return
        elif state.active_token != token:
            return
        if state is not None and self._sessions.current is not state:
            return
        if kind == "turn_start":
            self._set_activity(f"running · turn {payload.get('turn', '?')}")
        elif kind == "compression":
            before = payload.get("before", 0)
            after = payload.get("after", 0)
            saved = before - after
            if saved > 0:
                self._total_reclaimed += saved

    def _on_run_complete(self, traj, state: SessionState | None, token: int) -> None:
        if state is None:
            if not self._is_current_chat_turn(token):
                return
            self._trajectory = traj
            self._chat_busy = False
            self._finalize_stream_widget()
            self._stop_activity_animation()
            self._stop_working_animation()
            self._finish_turn_metrics()
            run_end = [e for e in traj.events if e.type == "run_end"]
            reason = run_end[0].payload.get("reason", "?") if run_end else "?"
            self._set_activity(f"done · {reason}")
            return
        if state.active_token != token:
            return
        if state.run_id != traj.run_id:
            old_id = state.run_id
            self._sessions.rename(old_id, traj.run_id)
            worker = self._session_workers.pop(old_id, None)
            if worker is not None:
                self._session_workers[traj.run_id] = worker
            self._refresh_sidebar()
        state.busy = False
        state.active_token = None
        self._trajectory = traj
        if not state.title or state.title == "新会话":
            self._schedule_title_summary(state, traj)
        if self._sessions.current is state:
            self._finalize_stream_widget()
            self._stop_activity_animation()
            self._stop_working_animation()
            self._finish_turn_metrics()
            run_end = [e for e in traj.events if e.type == "run_end"]
            if run_end:
                reason = run_end[0].payload.get("reason", "?")
            else:
                last_turn = max((e.turn or 0 for e in traj.events), default=0)
                reason = f"chat turn {last_turn + 1}"
            self._set_activity(f"done · {reason}")
            if self._total_reclaimed:
                self._write_line(
                    f"上下文压缩已回收 {self._total_reclaimed / 1024:.1f}k tokens。",
                    kind=TuiEntryKind.SYSTEM,
                )
                self._total_reclaimed = 0
            pending = self._drain_guidance(state)
            if pending:
                text = "\n".join(pending)
                user_entry = state.transcript.add(TuiEntryKind.USER, text)
                output = self.query_one("#output", ConversationView)
                output.add_entry(user_entry)
                token = self._begin_session_turn(state)
                self.run_worker(
                    lambda: self._run_agent_worker(state, text, token),
                    thread=True,
                    exclusive=False,
                    name=f"agent-{state.run_id}",
                    group="agent",
                )
        self._refresh_sidebar()

    def _on_agent_error(self, message: str, state: SessionState | None, token: int) -> None:
        if state is None:
            if not self._is_current_chat_turn(token):
                return
            self._chat_busy = False
            self._stop_activity_animation()
            self._stop_working_animation()
            self._finish_turn_metrics()
            self._set_activity("error")
            self._write_line(message, kind=TuiEntryKind.ERROR)
            return
        if state.active_token != token:
            return
        state.busy = False
        state.active_token = None
        self._refresh_sidebar()
        if self._sessions.current is state:
            self._stop_activity_animation()
            self._stop_working_animation()
            self._finish_turn_metrics()
            self._set_activity("error")
            self._write_line(message, kind=TuiEntryKind.ERROR)

    # ── Title summary (ADR-0026) ─────────────────────────────────────────────

    def _schedule_title_summary(self, state: SessionState, traj) -> None:
        task = ""
        reply = ""
        for ev in traj.events:
            if ev.type == "run_start":
                task = str(ev.payload.get("task") or "")
            elif ev.type == "llm_response" and not reply:
                for b in ev.payload.get("blocks", []):
                    if isinstance(b, dict) and b.get("type") == "text":
                        reply += str(b.get("text") or "")
        if not task:
            return
        self.run_worker(
            lambda: self._summarize_worker(state, task, reply),
            thread=True,
            exclusive=False,
            name="title-summary",
            group="title-summary",
        )

    def _summarize_worker(self, state: SessionState, task: str, reply: str) -> None:
        agent = state.agent
        if agent is None:
            return
        title = agent.summarize(task, reply)
        self.call_from_thread(self._apply_title, state, title, task)

    def _apply_title(self, state: SessionState, title: str, task: str) -> None:
        state.title = title or (task or "会话")[:15]
        self._save_session_title(state.run_id, state.title)
        self._refresh_sidebar()
        self._refresh_topbar()

    def _save_session_title(self, run_id: str, title: str) -> None:
        try:
            import sqlite3
            conn = sqlite3.connect(self._config.db_path, timeout=5)
            try:
                conn.execute("UPDATE runs SET title=? WHERE run_id=?", (title, run_id))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    # ── Permission bridge (serialized queue across sessions, ADR-0026) ───────

    def _thread_permission(
        self, op: Operation, decision: Decision, state: SessionState | None = None,
    ) -> Decision:
        if self._loop is None:
            return Decision.DENY
        with self._permission_lock:
            self._permission_queue.append(op)
        try:
            deadline = time.time() + 120.0
            while True:
                with self._permission_lock:
                    is_front = bool(self._permission_queue) and self._permission_queue[0] is op
                if is_front:
                    break
                if time.time() > deadline:
                    with self._permission_lock:
                        if op in self._permission_queue:
                            self._permission_queue.remove(op)
                    return Decision.DENY
                time.sleep(0.1)
            future = asyncio.run_coroutine_threadsafe(
                self._show_permission_async(op, state),
                self._loop,
            )
            return future.result(timeout=120.0)
        except Exception:
            return Decision.DENY
        finally:
            with self._permission_lock:
                if op in self._permission_queue:
                    self._permission_queue.remove(op)

    async def _show_permission_async(self, op: Operation, state: SessionState | None = None) -> Decision:
        from vague_code.tui.screens.permission import PermissionDialog

        session_label = state.run_id[:8] if state is not None else "task"
        worker = self.run_worker(
            self._show_permission_worker(op, PermissionDialog, session_label),
            name="permission-dialog",
            exit_on_error=False,
        )
        return await worker.wait()

    async def _show_permission_worker(self, op: Operation, dialog_cls, session_label: str) -> Decision:
        dialog = dialog_cls(op, session_label=session_label)
        decision = await self.push_screen_wait(dialog)
        if decision == Decision.ALLOW and dialog.always_allow:
            pattern = f"{op.tool_name} {op.input}"
            self._save_permission_rule(pattern, "allow")
        return decision

    def _load_permission_rules(self) -> list[dict]:
        try:
            if self._rules_path.is_file():
                return json.loads(self._rules_path.read_text(encoding="utf-8"))
        except Exception:
            import warnings
            warnings.warn(f"Failed to load permission rules from {self._rules_path}", stacklevel=2)
        return []

    def _save_permission_rule(self, pattern: str, action: str = "allow") -> None:
        rules = self._load_permission_rules()
        rules.append({"pattern": pattern, "action": action})
        try:
            self._rules_path.parent.mkdir(parents=True, exist_ok=True)
            self._rules_path.write_text(
                json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception:
            import warnings
            warnings.warn(f"Failed to save permission rules to {self._rules_path}", stacklevel=2)

    # ── Actions ──────────────────────────────────────────────────────────────

    async def action_copy_or_interrupt(self) -> None:
        focused = self.focused
        if isinstance(focused, TextArea) and focused.selected_text:
            focused.action_copy()
            return
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            selected = None
        if selected:
            self.copy_to_clipboard(selected)
            return
        if self._chat_busy:
            self._interrupt_chat_turn()
            return
        self._write_line(
            "无选中文本可复制 · 退出请输入 exit", kind=TuiEntryKind.SYSTEM
        )

from __future__ import annotations

import asyncio
import json
import threading
import time
from inspect import isawaitable
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Key
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Static, TextArea
from textual.worker import get_current_worker

from src.agent.ir import (
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
from src.agent.permission import Decision, Operation
from src.tui.commands import (
    CompositeCommandHandler,
    HelpCommandHandler,
    ModelCommandHandler,
    PermissionCommandHandler,
    SessionCommandHandler,
    picker_command,
)
from src.tui.mixin import XClawViewMixin
from src.tui.picker import TuiPickerItem, TuiPickerState, render_picker
from src.tui.runner import XClawAgentRunner
from src.tui.state import TuiEntryKind, TuiTranscript
from src.tui.views.activity import compact_tool_content
from src.tui.views.topbar import topbar_markup
from src.tui.views.welcome import welcome_renderable
from src.tui.widgets import ComposerTextArea, XClawScreen, _plain_static
from src.tui.widgets.conversation import ConversationView
from src.tui.widgets.status import ActivityLine

PICKER_VISIBLE_LIMIT = 8


class XClawApp(XClawViewMixin, App):
    CSS_PATH = "theme.tcss"
    ALLOW_SELECT = True

    BINDINGS = [
        Binding("ctrl+c", "copy_output_or_quit", "Copy / interrupt / quit", priority=True),
        Binding("t", "toggle_thinking", "Toggle thinking"),
        Binding("f1", "show_help", "Help", show=False),
    ]

    WELCOME_PARTICLE_INTERVAL_SECONDS = 0.85
    COMPACT_WELCOME_MAX_WIDTH = 80
    COMPACT_WELCOME_MAX_HEIGHT = 24
    ESC_INTERRUPT_WINDOW_SECONDS = 1.0

    def get_default_screen(self) -> Screen:
        return XClawScreen(id="_default")

    def __init__(
        self,
        *,
        config,
        backend,
        task: str = "",
        workdir: str = ".",
    ) -> None:
        super().__init__()
        self._config = config
        self._backend = backend
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
        self.transcript = TuiTranscript()
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
        self._pending_guidance: list[str] = []
        self._guidance_lock = threading.Lock()
        self._reasoning_full: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        yield Static(self._topbar_text(), id="topbar", classes="topbar")
        with Vertical(id="main"):
            yield ConversationView(id="output")
            yield ActivityLine("idle · ready", id="activity", classes="activity-line")
            with Vertical(id="composer", classes="composer"):
                yield ComposerTextArea(
                    placeholder="输入消息，Enter 发送，Shift+Enter 换行，Ctrl+C 退出",
                    id="input",
                    show_line_numbers=False,
                    soft_wrap=True,
                    compact=True,
                )

    def on_mount(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.title = f"XClaw — {self._workdir}"
        self._show_welcome()
        if self._agent_task:
            self._submit_task(self._agent_task)
            self._agent_task = ""

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
        provider = str(
            getattr(self._backend, "name", None)
            or getattr(self._backend, "provider", None)
            or "?"
        )
        model = self._config.model
        mode = self._config.permission_mode
        cwd = Path(self._workdir).resolve().name or "."
        width = max(0, self.size.width - 4)
        return topbar_markup(self._activity_text, provider, model, mode, cwd, width)

    def _refresh_topbar(self) -> None:
        topbar = self._query_mounted("#topbar")
        if topbar is not None and hasattr(topbar, "update"):
            topbar.update(self._topbar_text())

    def _welcome_compact(self) -> bool:
        return self.size.width < self.COMPACT_WELCOME_MAX_WIDTH or self.size.height < self.COMPACT_WELCOME_MAX_HEIGHT

    def _show_welcome(self) -> None:
        if self._welcome_widget is not None:
            return
        widget = _plain_static("", id="welcome", classes="welcome")
        widget.update(welcome_renderable(compact=self._welcome_compact(), particle_frame=0))
        self._welcome_widget = widget
        output = self.query_one("#output")
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
        if self._picker is not None and text.isdigit():
            if self._picker_select_number(int(text)):
                return

        if text.startswith("/"):
            self._handle_slash(text)
            return

        self._agent_task = text
        user_entry = self.transcript.add(TuiEntryKind.USER, text)
        output = self.query_one("#output", ConversationView)
        output.add_entry(user_entry)
        if self._chat_busy:
            self._add_guidance(text)
            self._write_line("已加入运行队列，将在下一轮生效。", kind=TuiEntryKind.SYSTEM)
            return
        token = self._begin_chat_turn(text)
        self.run_worker(
            lambda: self._run_agent_worker(text, token),
            thread=True,
            exclusive=True,
            name="agent",
        )

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
            self._clear_output()
            if output:
                self._write_line(output, kind=TuiEntryKind.COMMAND)
            self._show_welcome()
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
        if action_type == "model_changed":
            model = str(action.get("model") or "")
            if model:
                self._config.model = model
                self._refresh_topbar()
            return True
        return False

    def _clear_output(self) -> None:
        output = self.query_one("#output", ConversationView)
        output.clear()
        self.transcript = output.transcript

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
        if self._picker is not None and self._handle_picker_key(event):
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
        if not self._chat_busy:
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

    def _start_resume(self, run_id: str) -> None:
        if self._chat_busy:
            self._write_line("Agent 正在运行，请先等待或中断。", kind=TuiEntryKind.SYSTEM)
            return
        try:
            from src.agent.trajectory import Trajectory
            traj = Trajectory.from_db(run_id, self._config.db_path)
        except Exception as e:
            self._write_line(f"Resume failed: {e}", kind=TuiEntryKind.ERROR)
            return
        self._picker = None
        self._clear_output()
        self._replay_trajectory(traj)
        token = self._begin_chat_turn(f"resume {run_id}")
        self._write_line(f"正在恢复会话 {run_id}…", kind=TuiEntryKind.COMMAND)
        self.run_worker(
            lambda: self._run_resume_worker(run_id, traj, token),
            thread=True,
            exclusive=True,
            name="agent",
        )

    def _replay_trajectory(self, traj) -> None:
        from src.agent.trajectory import EventType
        from src.tui.state import TuiTranscriptEntry
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

    def _run_agent_worker(self, text: str, token: int) -> None:
        worker = get_current_worker()
        runner = XClawAgentRunner(
            config=self._config,
            backend=self._backend,
            permission_rules=self._load_permission_rules(),
            on_stream_event=lambda ev: self.call_from_thread(
                self._on_stream_event, ev, token
            ),
            on_tool_result=lambda tid, name, content, err: self.call_from_thread(
                self._on_tool_result, tid, name, content, err, token
            ),
            on_state_change=lambda kind, payload: self.call_from_thread(
                self._on_state_change, kind, payload, token
            ),
            on_permission=self._thread_permission,
            on_run_complete=lambda traj: self.call_from_thread(
                self._on_run_complete, traj, token
            ),
            on_error=lambda msg: self.call_from_thread(self._on_agent_error, msg, token),
            is_cancelled=lambda: worker.is_cancelled,
            guidance_provider=self._drain_guidance,
        )
        runner.run_task(text, self._workdir)

    def _run_resume_worker(self, run_id: str, traj, token: int) -> None:
        worker = get_current_worker()
        if worker.is_cancelled:
            return
        try:
            runner = XClawAgentRunner(
                config=self._config,
                backend=self._backend,
                permission_rules=self._load_permission_rules(),
                on_stream_event=lambda ev: self.call_from_thread(
                    self._on_stream_event, ev, token
                ),
                on_tool_result=lambda tid, name, content, err: self.call_from_thread(
                    self._on_tool_result, tid, name, content, err, token
                ),
                on_state_change=lambda kind, payload: self.call_from_thread(
                    self._on_state_change, kind, payload, token
                ),
                on_permission=self._thread_permission,
                on_run_complete=lambda result: self.call_from_thread(
                    self._on_run_complete, result, token
                ),
                on_error=lambda msg: self.call_from_thread(self._on_agent_error, msg, token),
                is_cancelled=lambda: worker.is_cancelled,
                guidance_provider=self._drain_guidance,
            )
            runner.resume(traj)
        except Exception as e:
            self.call_from_thread(self._on_agent_error, f"Resume failed: {e}", token)

    # ── Guidance queue ────────────────────────────────────────────────────────

    def _add_guidance(self, text: str) -> None:
        with self._guidance_lock:
            self._pending_guidance.append(text)

    def _drain_guidance(self) -> list[str]:
        with self._guidance_lock:
            guidance = list(self._pending_guidance)
            self._pending_guidance.clear()
        return guidance

    def _has_pending_guidance(self) -> bool:
        with self._guidance_lock:
            return bool(self._pending_guidance)

    # ── Event dispatch (UI thread) ───────────────────────────────────────────

    def _on_stream_event(self, ev: StreamEvent, token: int) -> None:
        if not self._is_current_chat_turn(token):
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
        from src.tui.commands.handlers import _HELP_TEXT
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
        self, tool_id: str, tool_name: str, content: str, is_error: bool, token: int
    ) -> None:
        if not self._is_current_chat_turn(token):
            return
        self._running_tool_call_ids.discard(tool_id)
        self._turn_tool_count = len(self._running_tool_call_ids) or self._turn_tool_count
        entry = self._tool_entries.pop(tool_id, None)
        status = "error" if is_error else "success"
        summary = compact_tool_content(content)
        suffix = f"：{summary}" if summary else ""
        text = f"工具{'失败' if is_error else '完成'}：{tool_name}{suffix}"
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

    def _on_state_change(self, kind: str, payload: dict, token: int) -> None:
        if not self._is_current_chat_turn(token):
            return
        if kind == "turn_start":
            self._set_activity(f"running · turn {payload.get('turn', '?')}")
        elif kind == "compression":
            before = payload.get("before", 0)
            after = payload.get("after", 0)
            saved = before - after
            if saved > 0:
                self._total_reclaimed += saved

    def _on_run_complete(self, traj, token: int) -> None:
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
        pending = self._drain_guidance()
        if pending:
            text = "\n".join(pending)
            user_entry = self.transcript.add(TuiEntryKind.USER, text)
            output = self.query_one("#output", ConversationView)
            output.add_entry(user_entry)
            token = self._begin_chat_turn(text)
            self.run_worker(
                lambda: self._run_agent_worker(text, token),
                thread=True,
                exclusive=True,
                name="agent",
            )

    def _on_agent_error(self, message: str, token: int) -> None:
        if not self._is_current_chat_turn(token):
            return
        self._chat_busy = False
        self._stop_activity_animation()
        self._stop_working_animation()
        self._finish_turn_metrics()
        self._set_activity("error")
        self._write_line(message, kind=TuiEntryKind.ERROR)

    # ── Permission bridge ────────────────────────────────────────────────────

    def _thread_permission(self, op: Operation, decision: Decision) -> Decision:
        if self._loop is None:
            return Decision.DENY
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._show_permission_async(op),
                self._loop,
            )
            return future.result(timeout=120.0)
        except Exception:
            return Decision.DENY

    async def _show_permission_async(self, op: Operation) -> Decision:
        from src.tui.screens.permission import PermissionDialog

        worker = self.run_worker(
            self._show_permission_worker(op, PermissionDialog),
            name="permission-dialog",
            exit_on_error=False,
        )
        return await worker.wait()

    async def _show_permission_worker(self, op: Operation, dialog_cls) -> Decision:
        dialog = dialog_cls(op)
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

    async def action_copy_output_or_quit(self) -> None:
        focused = self.focused
        if isinstance(focused, TextArea) and focused.selected_text:
            focused.action_copy()
            return
        if self._chat_busy:
            self._interrupt_chat_turn()
            return
        result = self.action_quit()
        if isawaitable(result):
            await result

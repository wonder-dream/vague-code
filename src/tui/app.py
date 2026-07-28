from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.worker import get_current_worker

from src.agent.backend import ModelBackend
from src.agent.config import AgentConfig
from src.agent.ir import StreamEvent, dispatch_event
from src.agent.loop import Agent, EventType
from src.agent.permission import Decision, Operation
from src.tui.screens.help import HelpScreen
from src.tui.screens.permission import PermissionDialog
from src.tui.visitor import TextualStreamVisitor
from src.tui.widgets.command_input import CommandInput
from src.tui.widgets.conversation import ConversationView
from src.tui.screens.session_detail import SessionDetail
from src.tui.widgets.sidebar import Sidebar
from src.tui.widgets.status_bar import StatusBar


class XClawApp(App):
    CSS_PATH = "theme.tcss"

    BINDINGS = [
        Binding("ctrl+c", "stop_agent", "Stop"),
        Binding("t", "toggle_thinking", "Think"),
        Binding("e", "toggle_expand", "Expand"),
        Binding("tab", "select_next", "Next"),
        Binding("shift+tab", "select_prev", "Prev"),
        Binding("slash", "focus_input", "Cmd"),
        Binding("escape", "cancel", "Back"),
        Binding("f1", "show_help", "Help", show=False),
    ]

    def __init__(
        self,
        config: AgentConfig,
        backend: ModelBackend,
        task: str,
        workdir: str,
    ) -> None:
        super().__init__()
        self._config = config
        self._backend = backend
        self._task = task
        self._workdir = workdir
        self._loop: asyncio.AbstractEventLoop | None = None
        self._agent: Agent | None = None
        self._visitor: TextualStreamVisitor | None = None
        self._rules_path = Path(workdir) / ".agent" / "permission-rules.json"
        self._trajectory = None
        self._total_reclaimed = 0

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Sidebar(id="sidebar", db_path=self._config.db_path)
            yield ConversationView(id="conversation")
        yield StatusBar(id="status-bar")
        yield CommandInput(id="command-input")

    def on_mount(self) -> None:
        self._loop = asyncio.get_running_loop()
        conv = self.query_one("#conversation", ConversationView)
        self._visitor = TextualStreamVisitor(conv)
        conv.add_task_message(self._task)
        self._start_agent()

    def _load_permission_rules(self) -> list[dict]:
        try:
            if self._rules_path.is_file():
                return json.loads(self._rules_path.read_text(encoding="utf-8"))
        except Exception:
            pass
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
            pass

    @work(thread=True, exclusive=True)
    def _start_agent(self) -> None:
        worker = get_current_worker()
        agent = Agent(self._config, self._backend)
        agent._on_permission = self._thread_permission
        agent.on_tool_result = self._thread_on_tool_result
        agent.on_state_change = self._thread_on_state_change
        for rule in self._load_permission_rules():
            agent.add_permission_rule(rule["pattern"], rule.get("action", "allow"))
        self._agent = agent

        self.call_from_thread(self._on_agent_started)
        handle = agent.start(self._task, self._workdir)
        for ev in handle:
            if worker.is_cancelled:
                handle.close()
                return
            self.call_from_thread(self._on_stream_event, ev)

        traj = handle.trajectory
        self.call_from_thread(self._on_run_complete, traj)

    def _on_agent_started(self) -> None:
        self.query_one("#status-bar", StatusBar).run_state = "running"

    def _on_stream_event(self, ev: StreamEvent) -> None:
        if self._visitor is None:
            return
        dispatch_event(ev, self._visitor)

    def _on_run_complete(self, traj) -> None:
        status = self.query_one("#status-bar", StatusBar)
        run_end = [e for e in traj.events if e.type == EventType.run_end]
        reason = run_end[0].payload.get("reason", "?") if run_end else "?"
        status.run_state = "done"
        status.turn_info = f"Done — {reason}"
        self._trajectory = traj
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.refresh()

    def _thread_permission(self, op: Operation, decision: Decision) -> Decision:
        future = asyncio.run_coroutine_threadsafe(
            self._show_permission_async(op),
            self._loop,
        )
        return future.result()

    async def _show_permission_async(self, op: Operation) -> Decision:
        dialog = PermissionDialog(op)
        decision = await self.push_screen_wait(dialog)
        if decision == Decision.ALLOW and dialog.always_allow:
            pattern = f"{op.tool_name} {op.input}"
            self._save_permission_rule(pattern, "allow")
            if self._agent:
                self._agent.add_permission_rule(pattern, "allow")
        return decision

    def _thread_on_tool_result(
        self, tool_name: str, content: str, is_error: bool
    ) -> None:
        self.call_from_thread(
            self._on_tool_result, tool_name, content, is_error
        )

    def _on_tool_result(self, tool_name: str, content: str, is_error: bool) -> None:
        conv = self.query_one("#conversation", ConversationView)
        conv.add_tool_result(tool_name, content, is_error)

    def _thread_on_state_change(self, kind: str, payload: dict) -> None:
        self.call_from_thread(self._on_state_change, kind, payload)

    def _on_state_change(self, kind: str, payload: dict) -> None:
        status = self.query_one("#status-bar", StatusBar)
        if kind == "turn_start":
            status.turn_info = f"Turn {payload['turn']}/{self._config.max_turns}"
        elif kind == "llm_response":
            usage = payload.get("usage", {})
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            status.token_info = f"In: {inp:,}  Out: {out:,}"
        elif kind == "compression":
            before = payload.get("before", 0)
            after = payload.get("after", 0)
            saved = before - after
            if saved > 0:
                self._total_reclaimed += saved
                status.compression_info = f"Reclaimed: {self._total_reclaimed:,}"

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_stop_agent(self) -> None:
        for w in self.workers:
            if w.state.name == "RUNNING":
                w.cancel()
        status = self.query_one("#status-bar", StatusBar)
        status.run_state = "idle"
        status.turn_info = "Stopped by user"
        self._trajectory = None
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.refresh()

    def action_focus_input(self) -> None:
        self.query_one("#command-input", CommandInput).focus()

    def action_cancel(self) -> None:
        if self.screen is not self:
            self.pop_screen()
        else:
            self.query_one("#command-input", CommandInput).focus()

    def action_toggle_thinking(self) -> None:
        conv = self.query_one("#conversation", ConversationView)
        conv.toggle_thinking()

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_expand(self) -> None:
        conv = self.query_one("#conversation", ConversationView)
        conv.toggle_current_expand()

    def action_select_next(self) -> None:
        conv = self.query_one("#conversation", ConversationView)
        conv.select_next()

    def action_select_prev(self) -> None:
        conv = self.query_one("#conversation", ConversationView)
        conv.select_prev()

    # ── Sidebar message handling ────────────────────────────────────────────

    async def on_sidebar_session_selected(self, message: Sidebar.SessionSelected) -> None:
        for w in self.workers:
            if w.state.name == "RUNNING":
                w.cancel()
        result = await self.push_screen_wait(
            SessionDetail(message.run_id, self._config.db_path)
        )
        if result == "resume":
            await self._do_resume(message.run_id)
        elif result == "deleted":
            self.query_one("#sidebar", Sidebar).refresh()

    async def _do_resume(self, run_id: str) -> None:
        try:
            from src.agent.trajectory import Trajectory
            traj = Trajectory.from_db(run_id, self._config.db_path)
            conv = self.query_one("#conversation", ConversationView)
            conv.clear()
            agent = Agent(self._config, self._backend)
            agent._on_permission = self._thread_permission
            agent.on_tool_result = self._thread_on_tool_result
            agent.on_state_change = self._thread_on_state_change
            for rule in self._load_permission_rules():
                agent.add_permission_rule(rule["pattern"], rule.get("action", "allow"))
            self._agent = agent
            self._trajectory = agent.resume(traj)
            self.call_from_thread(self._on_run_complete, self._trajectory)
        except Exception as e:
            self.notify(f"Resume failed: {e}", severity="error")

    # ── Command handling ─────────────────────────────────────────────────────

    def on_command_input_submitted(self, message: CommandInput.Submitted) -> None:
        text = message.value.strip()
        self.query_one("#command-input", CommandInput).value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_slash(text)
        elif not any(w.state.name == "RUNNING" for w in self.workers):
            conv = self.query_one("#conversation", ConversationView)
            conv.clear()
            conv.add_task_message(text)
            self._task = text
            self._total_reclaimed = 0
            self._trajectory = None
            self._start_agent()
        else:
            self.notify("Agent is running — press Ctrl+C to stop first", severity="warning")

    def _handle_slash(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/mode":
            if arg in ("safe", "normal", "autoedit", "auto"):
                self._config.permission_mode = arg
                self.query_one("#status-bar", StatusBar).mode_info = f"Mode: {arg}"
                self.notify(f"Permission mode set to: {arg}")
                for w in self.workers:
                    if w.state.name == "RUNNING":
                        w.cancel()
                self._start_agent()
            else:
                self.notify(f"Unknown mode: {arg}", severity="error")
        elif cmd == "/save":
            if self._trajectory is None:
                self.notify("No trajectory to save", severity="error")
            else:
                path = arg or f"runs/{self._trajectory.run_id}.jsonl"
                try:
                    self._trajectory.export_jsonl(path)
                    self.notify(f"Saved to: {path}")
                except Exception as e:
                    self.notify(f"Save failed: {e}", severity="error")
        elif cmd == "/clear":
            conv = self.query_one("#conversation", ConversationView)
            conv.clear()
        elif cmd == "/help":
            self.push_screen(HelpScreen())
        elif cmd == "/quit":
            self.exit("User quit")
        else:
            self.notify(f"Unknown command: {cmd}", severity="error")

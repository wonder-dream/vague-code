from __future__ import annotations

import asyncio
import threading

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
        self._trajectory = None

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

    @work(thread=True, exclusive=True)
    def _start_agent(self) -> None:
        worker = get_current_worker()
        agent = Agent(self._config, self._backend)
        agent._on_permission = self._thread_permission
        agent.on_tool_result = self._thread_on_tool_result
        self._agent = agent

        handle = agent.start(self._task, self._workdir)
        for ev in handle:
            if worker.is_cancelled:
                handle.close()
                return
            self.call_from_thread(self._on_stream_event, ev)

        traj = handle.trajectory
        self.call_from_thread(self._on_run_complete, traj)

    def _on_stream_event(self, ev: StreamEvent) -> None:
        if self._visitor is None:
            return
        dispatch_event(ev, self._visitor)

    def _on_run_complete(self, traj) -> None:
        status = self.query_one("#status-bar", StatusBar)
        run_end = [e for e in traj.events if e.type == EventType.run_end]
        reason = run_end[0].payload.get("reason", "?") if run_end else "?"
        status.update(f"Run finished — reason: {reason}")
        self._trajectory = traj

    def _thread_permission(self, op: Operation, decision: Decision) -> Decision:
        future = asyncio.run_coroutine_threadsafe(
            self._show_permission_async(op),
            self._loop,
        )
        return future.result()

    async def _show_permission_async(self, op: Operation) -> Decision:
        dialog = PermissionDialog(op)
        result = await self.push_screen_wait(dialog)
        return result

    def _thread_on_tool_result(
        self, tool_name: str, content: str, is_error: bool
    ) -> None:
        self.call_from_thread(
            self._on_tool_result, tool_name, content, is_error
        )

    def _on_tool_result(self, tool_name: str, content: str, is_error: bool) -> None:
        conv = self.query_one("#conversation", ConversationView)
        conv.add_tool_result(tool_name, content, is_error)

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_stop_agent(self) -> None:
        for w in self.workers:
            if w.state.name == "RUNNING":
                w.cancel()
        self.query_one("#status-bar", StatusBar).update("Stopped by user")
        self._trajectory = None

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

    # ── Command handling ─────────────────────────────────────────────────────

    def on_command_input_submitted(self, message: CommandInput.Submitted) -> None:
        text = message.value.strip()
        self.query_one("#command-input", CommandInput).value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_slash(text)

    def _handle_slash(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/mode":
            if arg in ("safe", "normal", "autoedit", "auto"):
                self._config.permission_mode = arg
                self.query_one("#status-bar", StatusBar).mode_info = f"Mode: {arg}"
                self.notify(f"Permission mode set to: {arg}")
            else:
                self.notify(f"Unknown mode: {arg}", severity="error")
        elif cmd == "/clear":
            conv = self.query_one("#conversation", ConversationView)
            conv.remove_children()
        elif cmd == "/help":
            self.push_screen(HelpScreen())
        elif cmd == "/quit":
            self.exit("User quit")
        else:
            self.notify(f"Unknown command: {cmd}", severity="error")

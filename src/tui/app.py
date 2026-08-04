from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Static

from src.tui.state import TuiEntryKind, TuiTranscript
from src.tui.views.topbar import topbar_markup
from src.tui.views.welcome import welcome_renderable
from src.tui.widgets import ComposerTextArea, XClawScreen, _plain_static
from src.tui.widgets.conversation import ConversationView
from src.tui.widgets.status import ActivityLine


class XClawApp(App):
    CSS_PATH = "theme.tcss"
    ALLOW_SELECT = True

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("escape", "focus_input", "Focus input"),
    ]

    WELCOME_PARTICLE_INTERVAL_SECONDS = 0.85
    COMPACT_WELCOME_MAX_WIDTH = 80
    COMPACT_WELCOME_MAX_HEIGHT = 24

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
        self.transcript = TuiTranscript()
        self._welcome_widget: Static | None = None
        self._welcome_particle_timer: Timer | None = None
        self._welcome_particle_frame = 0
        self._activity_text = "idle · ready"

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
        self.title = f"XClaw — {self._workdir}"
        self._show_welcome()

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
        topbar = self.query_one("#topbar", Static)
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
        user_entry = self.transcript.add(TuiEntryKind.USER, text)
        self._append_entry(user_entry)
        self._run_task(text)

    def _run_task(self, text: str) -> None:
        self._agent_task = text
        entry = self.transcript.add(
            TuiEntryKind.SYSTEM,
            "Agent 引擎将在下一里程碑接入。",
        )
        self._append_entry(entry)

    def _append_entry(self, entry) -> None:
        output = self.query_one("#output", ConversationView)
        output.add_entry(entry)

    def action_focus_input(self) -> None:
        self.query_one("#input").focus()

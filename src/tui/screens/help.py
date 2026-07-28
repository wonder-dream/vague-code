from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class HelpScreen(ModalScreen):
    """Help screen showing key bindings and slash commands."""

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]XClaw TUI — Help[/bold]\n\n"
            "[bold]Key Bindings[/bold]\n"
            "  Ctrl+C      Stop agent\n"
            "  T           Toggle thinking visibility\n"
            "  /           Focus command input\n"
            "  Escape      Cancel / dismiss\n\n"
            "[bold]Slash Commands[/bold]\n"
            "  /mode <m>   Set permission mode (safe|normal|autoedit|auto)\n"
            "  /clear      Clear conversation\n"
            "  /help       Show this help\n"
            "  /quit       Exit TUI\n\n"
            "Press any key to close.",
            id="help-content",
        )

    def on_key(self, event) -> None:
        self.dismiss()

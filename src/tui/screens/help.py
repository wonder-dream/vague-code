from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static


class HelpScreen(ModalScreen):
    """Help screen showing key bindings and slash commands."""

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]XClaw TUI — Help[/bold]\n\n"
            "[bold]Key Bindings[/bold]\n"
            "  Ctrl+C      Stop agent\n"
            "  T           Toggle thinking fold/expand\n"
            "  E           Expand/collapse focused block\n"
            "  Tab         Next foldable block\n"
            "  Shift+Tab   Previous foldable block\n"
            "  /           Focus command input\n"
            "  Escape      Cancel / dismiss\n"
            "  F1          Show this help\n\n"
            "[bold]Slash Commands[/bold]\n"
            "  /mode <m>     Set permission mode: safe|normal|autoedit|auto\n"
            "  /clear        Clear conversation view\n"
            "  /save [path]  Export trajectory to JSONL file\n"
            "  /help         Show this help\n"
            "  /quit         Exit TUI\n\n"
            "Press any key to close.",
            id="help-content",
        )

    def on_key(self, event) -> None:
        self.dismiss()

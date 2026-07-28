from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from src.agent.permission import Decision, Operation


class PermissionDialog(ModalScreen[Decision]):
    """Modal screen asking user to approve or deny a tool execution.

    Returns Decision.ALLOW or Decision.DENY.
    Check .always_allow after dismiss to see if user chose to persist.
    """

    def __init__(self, operation: Operation, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._op = operation
        self.always_allow: bool = False

    def compose(self) -> ComposeResult:
        cmd_display = self._op.command or str(self._op.input)[:120]
        with Vertical(id="permission-dialog"):
            yield Label("[bold]Permission Required[/]", id="perm-title")
            yield Label(f"Tool: [bold blue]{self._op.tool_name}[/]", id="perm-tool")
            yield Label(f"Details: [dim]{cmd_display}[/]", id="perm-detail")
            with Horizontal(id="perm-buttons"):
                yield Button("Allow Once (Y)", variant="primary", id="perm-allow")
                yield Button("Always Allow (Ctrl+Y)", variant="success", id="perm-allow-always")
                yield Button("Deny (N)", variant="error", id="perm-deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "perm-allow-always":
            self.always_allow = True
        self.dismiss(Decision.ALLOW if "allow" in (event.button.id or "") else Decision.DENY)

    def on_key(self, event) -> None:
        if event.key in ("y", "Y"):
            self.dismiss(Decision.ALLOW)
        elif event.key == "ctrl+y":
            self.always_allow = True
            self.dismiss(Decision.ALLOW)
        elif event.key in ("n", "N", "escape"):
            self.dismiss(Decision.DENY)

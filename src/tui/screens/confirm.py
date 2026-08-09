"""Generic confirmation dialog (ModalScreen[bool]) for destructive actions."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from src.tui.widgets.common import _plain_static


class ConfirmDialog(ModalScreen[bool]):
    """Ask the user to confirm a destructive action; returns True/False.

    Keyboard: y/enter confirm, n/esc cancel. Focus lands on Cancel.
    """

    def __init__(self, title: str, message: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._title = title
        self._message = message

    def on_mount(self) -> None:
        self.query_one("#confirm-cancel", Button).focus()

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(
                f"[#c85f5f bold]{escape(self._title)}[/]",
                id="confirm-title",
            )
            yield _plain_static(self._message, id="confirm-message", classes="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button(" 确认删除 (Y) ", variant="error", id="confirm-ok")
                yield Button(" 取消 (N) ", variant="default", id="confirm-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-ok":
            self.dismiss(True)
        elif event.button.id == "confirm-cancel":
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key in ("y", "Y", "enter"):
            self.dismiss(True)
        elif event.key in ("n", "N", "escape"):
            self.dismiss(False)

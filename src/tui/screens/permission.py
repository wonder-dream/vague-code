from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea

from src.agent.permission import Decision, Operation
from src.tui.views.review import render_prewrite_review


class PermissionDialog(ModalScreen[Decision]):
    """Modal screen asking user to approve or deny a tool execution.

    Shows a pre-write diff when available; denial may carry an optional
    feedback reason that is passed back to the model.
    """

    def __init__(self, operation: Operation, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._op = operation
        self.always_allow: bool = False

    def compose(self) -> ComposeResult:
        cmd_display = self._op.command or str(self._op.input)[:120]
        with Vertical(id="permission-dialog"):
            yield Label("[bold]Permission Required[/]", id="perm-title")
            yield Label(f"Tool: [bold]{escape(self._op.tool_name)}[/]", id="perm-tool")
            yield Label(f"Details: [dim]{escape(cmd_display)}[/]", id="perm-detail")
            if self._op.review:
                review = self._op.review
                rendered = render_prewrite_review(review) if isinstance(review, dict) else Text("")
                with VerticalScroll(id="perm-review-scroll", classes="review-scroll"):
                    yield Static(rendered, id="perm-review", classes="review-card")
            yield Label("Deny feedback (optional):", id="perm-feedback-label")
            yield TextArea(
                "",
                id="perm-feedback",
                show_line_numbers=False,
                placeholder="拒绝理由，将回传给模型",
                soft_wrap=True,
            )
            with Horizontal(id="perm-buttons"):
                yield Button("Allow Once (Y)", variant="primary", id="perm-allow")
                yield Button("Always Allow (Ctrl+Y)", variant="success", id="perm-allow-always")
                yield Button("Deny (N)", variant="error", id="perm-deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "perm-allow-always":
            self.always_allow = True
            self.dismiss(Decision.ALLOW)
        elif event.button.id == "perm-allow":
            self.dismiss(Decision.ALLOW)
        elif event.button.id == "perm-deny":
            self._apply_feedback()
            self.dismiss(Decision.DENY)

    def _apply_feedback(self) -> None:
        feedback = self.query_one("#perm-feedback", TextArea)
        text = feedback.text.strip()
        if text:
            self._op.feedback = text

    def on_key(self, event) -> None:
        if getattr(self.focused, "id", None) == "perm-feedback":
            return
        if event.key in ("y", "Y"):
            self.dismiss(Decision.ALLOW)
        elif event.key == "ctrl+y":
            self.always_allow = True
            self.dismiss(Decision.ALLOW)
        elif event.key in ("n", "N", "escape"):
            self._apply_feedback()
            self.dismiss(Decision.DENY)

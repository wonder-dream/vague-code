from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea

from vague_code.agent.permission import Decision, Operation
from vague_code.tui.views.permission import (
    permission_title,
    render_operation_body,
    tool_accent,
    tool_glyph,
)
from vague_code.tui.views.review import render_prewrite_review


class PermissionDialog(ModalScreen[Decision]):
    """Modal screen asking user to approve or deny a tool execution.

    Structured layout: accent header, operation body, pre-write diff card,
    optional deny-feedback input, action buttons. Denial may carry an
    optional feedback reason that is passed back to the model.
    """

    def __init__(self, operation: Operation, *args, session_label: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._op = operation
        self.always_allow: bool = False
        self._session_label = session_label

    def on_mount(self) -> None:
        self.query_one("#perm-allow", Button).focus()

    def compose(self) -> ComposeResult:
        op = self._op
        accent = tool_accent(op.tool_name)
        glyph = tool_glyph(op.tool_name)
        session_tag = f"  [#303238]会话 {escape(self._session_label)}[/]" if self._session_label else ""
        header = (
            f"[{accent} bold]{escape(glyph)} {escape(permission_title(op.tool_name))}[/]"
            f"  [#808185]{escape(op.tool_name)}[/]{session_tag}"
        )
        with Vertical(id="permission-dialog"):
            yield Static(header, id="perm-header")
            yield Static(render_operation_body(op), id="perm-body", classes="perm-body")
            if op.review:
                review = op.review
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
                yield Button(" Allow Once (Y) ", variant="success", id="perm-allow")
                yield Button(" Always Allow (Ctrl+Y) ", variant="primary", id="perm-allow-always")
                yield Button(" Deny (N) ", variant="error", id="perm-deny")

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

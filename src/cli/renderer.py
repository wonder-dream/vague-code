from __future__ import annotations

from rich.console import Console

from src.agent.ir import (
    MessageEnd,
    MessageStart,
    NullVisitor,
    RetryNotice,
    TextDelta,
    ThinkingDelta,
    ToolUseStart,
)


class RichStreamVisitor(NullVisitor):
    """Renders StreamEvent sequence to a Rich console in real time."""

    def __init__(self, console: Console, verbose: bool = False):
        self._console = console
        self._verbose = verbose
        self.model: str | None = None

    def message_start(self, ev: MessageStart) -> None:
        self.model = ev.model
        if self._verbose:
            self._console.print(f"[dim]model: {ev.model}[/dim]")

    def thinking_delta(self, ev: ThinkingDelta) -> None:
        self._console.print(ev.delta, end="", style="dim")

    def text_delta(self, ev: TextDelta) -> None:
        self._console.print(ev.delta, end="")

    def tool_use_start(self, ev: ToolUseStart) -> None:
        self._console.print(f"\n[tool] {ev.name}")

    def retry_notice(self, ev: RetryNotice) -> None:
        self._console.print(f"\n[yellow]retry: {ev.reason}, {ev.delay_s:.1f}s later (attempt {ev.attempt})[/yellow]")

    def message_end(self, ev: MessageEnd) -> None:
        self._console.print()

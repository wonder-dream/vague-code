from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static

from src.agent.ir import RetryNotice


class ConversationView(VerticalScroll):
    """Scrollable conversation view that renders stream events in real time."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thinking_content: list[str] = []
        self._thinking_block: Static | None = None
        self._tool_block: Static | None = None
        self._tool_args: list[str] = []
        self._streaming_block: Static | None = None

    def _get_or_create_stream(self) -> Static:
        if self._streaming_block is None:
            self._streaming_block = Static("", classes="text-delta")
            self.mount(self._streaming_block)
        return self._streaming_block

    def append_text(self, delta: str) -> None:
        block = self._get_or_create_stream()
        block.update((block.renderable or "") + delta)
        self.scroll_end(animate=False)

    def start_thinking(self) -> None:
        self._thinking_content = []

    def add_thinking_delta(self, delta: str) -> None:
        self._thinking_content.append(delta)
        self._update_thinking_placeholder()

    def end_thinking(self) -> None:
        if not self._thinking_content:
            return
        text = "".join(self._thinking_content)
        self._thinking_block = Static(
            f"[dim]<thinking> — {len(text)//4} tokens, press T to expand[/]",
            classes="thinking-block",
        )
        self.mount(self._thinking_block)
        self._thinking_content.clear()

    def _update_thinking_placeholder(self) -> None:
        pass

    def toggle_thinking(self) -> None:
        if self._thinking_block is None:
            return
        text = self._thinking_block.renderable
        full = "".join(self._thinking_content) if self._thinking_content else ""
        if not full:
            return
        if "expanded" in (self._thinking_block.classes or ""):
            self._thinking_block.classes = "thinking-block"
            self._thinking_block.update(f"[dim]<thinking> — {len(full)//4} tokens, press T to expand[/]")
        else:
            self._thinking_block.classes = "thinking-block expanded"
            self._thinking_block.update(f"[dim]{full}[/dim]")
        self.scroll_end(animate=False)

    def start_tool(self, name: str) -> None:
        self._streaming_block = None
        args_preview = f"[bold blue]🔧 {name}(...)[/]"
        self._tool_block = Static(args_preview, classes="tool-call")
        self.mount(self._tool_block)
        self.scroll_end(animate=False)

    def append_tool_args(self, delta: str) -> None:
        self._tool_args.append(delta)

    def add_tool_result(self, tool_name: str, content: str, is_error: bool) -> None:
        cls = "tool-result-error" if is_error else "tool-result"
        summary = content[:200].replace("\n", " ")
        widget = Static(f"  {'✗' if is_error else '✓'} {tool_name}: {summary}", classes=cls)
        self.mount(widget)
        self._tool_block = None
        self._tool_args.clear()
        self.scroll_end(animate=False)

    def add_retry_notice(self, ev: RetryNotice) -> None:
        msg = f"[yellow]↻ retry: {ev.reason}, {ev.delay_s:.1f}s (attempt {ev.attempt})[/]"
        self.mount(Static(msg, classes="retry-notice"))
        self.scroll_end(animate=False)

    def finalize_message(self, stop_reason: str) -> None:
        self._streaming_block = None

    def add_separator(self) -> None:
        self._streaming_block = None
        self.mount(Static("─" * 40, classes="separator"))
        self.scroll_end(animate=False)

    def add_task_message(self, text: str) -> None:
        self.mount(Static(f"[bold]> {text}[/]", classes="text-delta"))
        self.scroll_end(animate=False)

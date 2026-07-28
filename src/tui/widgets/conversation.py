from __future__ import annotations

from dataclasses import dataclass, field

from textual.containers import VerticalScroll
from textual.widgets import Static

from src.agent.ir import RetryNotice


@dataclass
class _FoldableBlock:
    widget: Static
    kind: str  # "thinking" | "tool_result" | "text"
    full_content: str = ""
    summary: str = ""
    collapsed: bool = True
    tool_name: str = ""
    is_error: bool = False


HEAD_LINES = 10
TAIL_LINES = 5


def _head_tail(content: str, head_n: int, tail_n: int) -> str:
    lines = content.splitlines(keepends=True)
    n = len(lines)
    if head_n + tail_n >= n:
        return content
    head = "".join(lines[:head_n])
    tail = "".join(lines[-tail_n:])
    return f"{head}\n...({n - head_n - tail_n} lines)...\n{tail}"


class ConversationView(VerticalScroll):
    """Scrollable conversation view with foldable thinking and tool result blocks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thinking_content: list[str] = []
        self._thinking_widget: Static | None = None
        self._tool_widget: Static | None = None
        self._tool_args: list[str] = []
        self._streaming_block: Static | None = None
        self._blocks: list[_FoldableBlock] = []
        self._current_focus: int | None = None

    def _get_or_create_stream(self) -> Static:
        if self._streaming_block is None:
            self._streaming_block = Static("", classes="text-delta")
            self.mount(self._streaming_block)
        return self._streaming_block

    def append_text(self, delta: str) -> None:
        block = self._get_or_create_stream()
        current = block._renderable if hasattr(block, '_renderable') else ""
        block.update(str(current) + delta)
        self.scroll_end(animate=False)

    def start_thinking(self) -> None:
        self._thinking_content = []

    def add_thinking_delta(self, delta: str) -> None:
        self._thinking_content.append(delta)

    def end_thinking(self) -> None:
        text = "".join(self._thinking_content)
        if not text:
            return
        summary = f"[dim]<thinking> — {len(text)//4} tokens, press T to expand[/]"
        self._thinking_widget = Static(summary, classes="thinking-block collapsed")
        self.mount(self._thinking_widget)
        self._blocks.append(_FoldableBlock(
            widget=self._thinking_widget,
            kind="thinking",
            full_content=text,
            summary=summary,
            collapsed=True,
        ))
        self._thinking_content.clear()
        self.scroll_end(animate=False)

    def _find_last_thinking_block(self) -> int | None:
        for i in range(len(self._blocks) - 1, -1, -1):
            if self._blocks[i].kind == "thinking":
                return i
        return None

    def toggle_thinking(self) -> None:
        idx = self._find_last_thinking_block()
        if idx is None:
            return
        self._toggle_block(idx)

    def _toggle_block(self, idx: int) -> None:
        blk = self._blocks[idx]
        if blk.collapsed:
            full = blk.full_content
            style = "dim" if blk.kind == "thinking" else ""
            display = f"[{style}]{full}[/{style}]" if style else full
            blk.widget.update(display)
            blk.widget.remove_class("collapsed")
            blk.widget.add_class("expanded")
        else:
            blk.widget.update(blk.summary)
            blk.widget.remove_class("expanded")
            blk.widget.add_class("collapsed")
        blk.collapsed = not blk.collapsed
        self.scroll_end(animate=False)

    def _foldable_indices(self) -> list[int]:
        return [i for i, b in enumerate(self._blocks) if b.kind in ("thinking", "tool_result")]

    def select_next(self) -> None:
        indices = self._foldable_indices()
        if not indices:
            return
        if self._current_focus is None:
            self._set_focus(indices[0])
        else:
            cur = indices.index(self._current_focus) if self._current_focus in indices else -1
            nxt = indices[(cur + 1) % len(indices)]
            self._set_focus(nxt)

    def select_prev(self) -> None:
        indices = self._foldable_indices()
        if not indices:
            return
        if self._current_focus is None:
            self._set_focus(indices[-1])
        else:
            cur = indices.index(self._current_focus) if self._current_focus in indices else 0
            prv = indices[(cur - 1) % len(indices)]
            self._set_focus(prv)

    def _set_focus(self, idx: int) -> None:
        if self._current_focus is not None and self._current_focus < len(self._blocks):
            old = self._blocks[self._current_focus]
            old.widget.remove_class("focused")
        self._current_focus = idx
        blk = self._blocks[idx]
        blk.widget.add_class("focused")
        self.scroll_to_widget(blk.widget)

    def toggle_current_expand(self) -> None:
        if self._current_focus is None or self._current_focus >= len(self._blocks):
            return
        self._toggle_block(self._current_focus)

    def start_tool(self, name: str) -> None:
        self._streaming_block = None
        self._tool_widget = Static(f"[bold blue]🔧 {name}(...)[/]", classes="tool-call")
        self.mount(self._tool_widget)
        self._tool_args = []
        self.scroll_end(animate=False)

    def append_tool_args(self, delta: str) -> None:
        self._tool_args.append(delta)

    def add_tool_result(self, tool_name: str, content: str, is_error: bool) -> None:
        cls = "tool-result-error" if is_error else "tool-result"
        summary = _head_tail(content, HEAD_LINES, TAIL_LINES)
        display_summary = summary.replace("\n", "\n  ").strip()
        icon = "✗" if is_error else "✓"
        header = f"  {icon} {tool_name}"
        if len(content) > 200:
            header += " — press E to expand"
        self._tool_widget = None
        display_text = f"{header}\n  {display_summary}"
        widget = Static(display_text, classes=cls)
        self.mount(widget)
        self._blocks.append(_FoldableBlock(
            widget=widget,
            kind="tool_result",
            full_content=content,
            summary=display_text,
            collapsed=True,
            tool_name=tool_name,
            is_error=is_error,
        ))
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

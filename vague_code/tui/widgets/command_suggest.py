"""命令候选浮层（CommandSuggest，ADR-0038）。

输入框键入 "/" 时在输入框上方显示命令候选列表；↑/↓ 移动高亮，Enter 选择，
Esc 关闭。默认 display:none，由 app 根据输入状态控制。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from vague_code.tui.commands.handlers import filter_commands


class CommandSuggest(Widget):
    """Slash 命令候选浮层（对齐 opencode / Claude Code 的 / 命令菜单）。"""

    DEFAULT_CSS = """
    CommandSuggest {
        display: none;
        height: auto;
        max-height: 8;
        border: round #3d4451;
        background: #1e2126;
        padding: 0 1;
    }
    CommandSuggest.visible {
        display: block;
    }
    CommandSuggest > .suggest-row {
        height: 1;
        color: #9aa0a6;
    }
    CommandSuggest > .suggest-row.suggest-selected {
        color: #7bba55;
        text-style: bold;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rows: list[Static] = []
        self._items: list[tuple[str, str, bool]] = []
        self.selected_index: int = 0
        self.visible_override: bool = False

    def compose(self) -> ComposeResult:
        yield Static("", classes="suggest-row")

    def on_mount(self) -> None:
        self._rows = [self.query_one(".suggest-row", Static)]

    def show_for(self, text: str) -> None:
        """按输入文本更新候选并决定显隐；返回是否显示。"""
        items = filter_commands(text)
        self.visible_override = bool(items)
        if not items:
            self._hide()
            return
        self._items = items
        if self.selected_index >= len(items):
            self.selected_index = 0
        self._render_rows()
        self._show()

    def _show(self) -> None:
        self.add_class("visible")

    def _hide(self) -> None:
        self.remove_class("visible")
        self._items = []

    def is_visible(self) -> bool:
        return self.has_class("visible")

    def _render_rows(self) -> None:
        rows = self._rows
        while len(rows) < len(self._items):
            row = Static("", classes="suggest-row")
            self.mount(row)
            rows.append(row)
        for index in range(len(rows)):
            row = rows[index]
            if index < len(self._items):
                cmd, desc, _ = self._items[index]
                row.update(f"  {cmd}  {desc}")
                row.set_class(index == self.selected_index, "suggest-selected")
                row.display = True
            else:
                row.display = False

    def move(self, delta: int) -> None:
        if not self._items:
            return
        self.selected_index = max(0, min(len(self._items) - 1, self.selected_index + delta))
        self._render_rows()

    def selected(self) -> tuple[str, str, bool] | None:
        if not self._items:
            return None
        index = max(0, min(self.selected_index, len(self._items) - 1))
        return self._items[index]

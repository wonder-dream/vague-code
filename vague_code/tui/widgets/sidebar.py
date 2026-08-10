"""Session sidebar: one row per chat session, keyboard navigable (ADR-0026).

The sidebar lists in-memory sessions (running/idle) plus recent chat sessions
from the DB. Rows are plain Static widgets updated in place (Textual mounts are
async, so we never rebuild the row list in the same frame); the app handles
up/down/enter on the focused sidebar.
"""

from __future__ import annotations

from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from rich.text import Text

from vague_code.tui.views.sidebar import session_row_text, DELETE_MARK

DELETE_ZONE_COLS = 2  # ✕ 标记的 cell 宽（含前导空格）


class SessionSidebar(VerticalScroll):
    can_focus = True

    class SessionSelected(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class SessionDeleteRequested(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._items: list[tuple[str, str, bool]] = []
        self._current_run_id: str | None = None
        self.selected_index: int = 0
        self._next_row_id = 0
        self._rows: list[Static] = []

    def compose(self):
        yield Static("Sessions", classes="sidebar-header")
        yield Vertical(id="session-rows")

    def update_sessions(self, items: list[tuple[str, str, bool]], current_run_id: str | None) -> None:
        """Sync the row list in place. Mount grows, stale rows are removed, text updates sync."""
        self._items = items
        self._current_run_id = current_run_id
        if not self.is_mounted:
            return
        if self.selected_index >= len(items):
            self.selected_index = max(0, len(items) - 1)
        while len(self._rows) < len(items):
            row = Static("", id=f"session-row-{self._next_row_id}")
            self._next_row_id += 1
            self.query_one("#session-rows", Vertical).mount(row)
            self._rows.append(row)
        while len(self._rows) > len(items):
            stale = self._rows.pop()
            setattr(stale, "run_id", None)
            stale.remove()
        for index, (run_id, title, busy) in enumerate(items):
            setattr(self._rows[index], "run_id", run_id)
            self._rows[index].update(
                session_row_text(title, busy, run_id == self._current_run_id) + DELETE_MARK
            )
        self._restyle_rows()

    def on_click(self, event) -> None:
        run_id = str(getattr(event.widget, "run_id", "") or "")
        if not run_id:
            return
        # 点击行尾 ✕ 区域 → 删除；否则切换会话
        if self._click_in_delete_zone(event):
            self.post_message(self.SessionDeleteRequested(run_id))
            return
        index = next(
            (i for i, (rid, _, _) in enumerate(self._items) if rid == run_id),
            None,
        )
        if index is not None:
            self.selected_index = index
            self._restyle_rows()
        self.post_message(self.SessionSelected(run_id))

    def _click_in_delete_zone(self, event) -> bool:
        """True when the click lands on the trailing ✕ (full-width-cell safe)."""
        rendered = event.widget.render()
        text = getattr(rendered, "text", None)
        if not isinstance(text, str):
            text = str(rendered)
        cell_len = Text.from_markup(text).cell_len if text else 0
        padding_left = 1  # theme.tcss: #session-rows > Static { padding: 0 1 0 1 }
        text_end = padding_left + cell_len
        return event.x >= text_end - DELETE_ZONE_COLS

    def _restyle_rows(self) -> None:
        for index, row in enumerate(self._rows):
            row.set_class(index == self.selected_index, "sidebar-selected")

    def move_selection(self, delta: int) -> None:
        if not self._items:
            return
        self.selected_index = max(0, min(len(self._items) - 1, self.selected_index + delta))
        self._restyle_rows()

    def select_current(self) -> str | None:
        if not self._items:
            return None
        index = max(0, min(self.selected_index, len(self._items) - 1))
        return self._items[index][0]

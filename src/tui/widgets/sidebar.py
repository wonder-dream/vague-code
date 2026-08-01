from __future__ import annotations

import sqlite3

from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static


class Sidebar(VerticalScroll):
    """Sidebar panel with interactive session list and recent episodic memory."""

    class SessionSelected(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(self, db_path: str = "runs/runs.db", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._db_path = db_path
        self._session_list: ListView | None = None
        self._session_rows_list: list[tuple[str, str, str]] = []

    def on_mount(self) -> None:
        self._build_sessions()
        self._build_memory()

    def reload(self) -> None:
        self.remove_children()
        self._build_sessions()
        self._build_memory()

    def _build_sessions(self) -> None:
        self.mount(Static("[bold]Recent Sessions[/]", classes="sidebar-header"))
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                "SELECT run_id, task, status FROM runs ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            conn.close()
        except Exception:
            rows = []

        if not rows:
            self.mount(Static("  (no sessions)", classes="sidebar-muted"))
            return

        items: list[ListItem] = []
        self._session_rows: dict[str, tuple[str, str]] = {}
        for run_id, task, status in rows:
            task_short = (task or "?").strip()[:40]
            icon = "●" if status == "in_progress" else "✓" if status == "end_turn" else "✗"
            label = Label(f"  {icon} {task_short}")
            items.append(ListItem(label))
            self._session_rows[str(run_id)] = (status, task or "")
        self._session_list = ListView(*items)
        self._session_rows_list = [(r, s, t) for r, s, t in zip(
            [r[0] for r in rows],
            [r[2] for r in rows],
            [r[1] or "" for r in rows],
        )]
        self.mount(self._session_list)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._session_list is None or not self._session_rows_list:
            return
        idx = self._session_list.index
        if idx is None or idx < 0 or idx >= len(self._session_rows_list):
            return
        run_id, _, _ = self._session_rows_list[idx]
        self.post_message(self.SessionSelected(run_id))

    def _build_memory(self) -> None:
        self.mount(Static("", classes="sidebar-spacer"))
        self.mount(Static("[bold]Memory[/]", classes="sidebar-header"))
        try:
            from src.agent.memory import MemoryStore
            store = MemoryStore("runs/memory.db")
            recents = store.recent(kind="episodic", limit=3)
            if recents:
                for r in recents:
                    text = r["content"][:60].replace("\n", " ")
                    self.mount(Static(f"  [E] {text}", classes="sidebar-item"))
            else:
                self.mount(Static("  (no memory)", classes="sidebar-muted"))
            store.close()
        except Exception:
            self.mount(Static("  (no memory)", classes="sidebar-muted"))

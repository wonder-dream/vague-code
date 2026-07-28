from __future__ import annotations

import sqlite3

from textual.containers import VerticalScroll
from textual.widgets import Static


class Sidebar(VerticalScroll):
    """Sidebar panel with session list and memory summary."""

    def __init__(self, db_path: str = "runs/runs.db", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._db_path = db_path

    def on_mount(self) -> None:
        self._load_sessions()
        self._load_memory()

    def _load_sessions(self) -> None:
        self.mount(Static("[bold]Recent Sessions[/]", classes="sidebar-header"))
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                "SELECT run_id, task, status FROM runs ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            conn.close()
            if not rows:
                self.mount(Static("  (no sessions)", classes="sidebar-muted"))
            for run_id, task, status in rows:
                task_short = (task or "?").strip()[:40]
                icon = "●" if status == "in_progress" else "✓" if status == "end_turn" else "✗"
                self.mount(Static(f"  {icon} {task_short}", classes="sidebar-item"))
        except Exception:
            self.mount(Static("  (no sessions)", classes="sidebar-muted"))

    def _load_memory(self) -> None:
        self.mount(Static("", classes="sidebar-spacer"))
        self.mount(Static("[bold]Memory[/]", classes="sidebar-header"))
        try:
            from src.agent.memory import MemoryStore
            store = MemoryStore("runs/memory.db")
            pinned = store.get_pinned()
            if pinned:
                for p in pinned:
                    text = p["content"][:60].replace("\n", " ")
                    self.mount(Static(f"  [P] {text}", classes="sidebar-item"))
            store.close()
        except Exception:
            self.mount(Static("  (no memory)", classes="sidebar-muted"))

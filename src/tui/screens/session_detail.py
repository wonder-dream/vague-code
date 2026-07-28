from __future__ import annotations

import json
import sqlite3

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class SessionDetail(ModalScreen):
    """Show session details and offer resume/delete actions."""

    def __init__(self, run_id: str, db_path: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._run_id = run_id
        self._db_path = db_path

    def compose(self) -> ComposeResult:
        info = self._load_info()
        with Vertical(id="session-detail"):
            yield Label(f"[bold]Session: {self._run_id}[/]", id="sd-title")
            yield Label(f"Task: [dim]{info.get('task', '?')}[/]")
            yield Label(f"Workdir: [dim]{info.get('workdir', '?')}[/]")
            yield Label(f"Status: [dim]{info.get('status', '?')}[/]")
            if info.get("turns"):
                yield Label(f"Turns: [dim]{info['turns']}[/]")
            if info.get("tokens"):
                yield Label(f"Tokens: [dim]{info['tokens']}[/]")
            with Horizontal(id="sd-buttons"):
                yield Button("Resume", variant="primary", id="sd-resume")
                yield Button("Delete", variant="error", id="sd-delete")
                yield Button("Back", id="sd-back")

    def _load_info(self) -> dict:
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute(
                "SELECT config_json, status FROM runs WHERE run_id=?", (self._run_id,)
            ).fetchone()
            if row is None:
                return {"status": "not found"}
            config_data = json.loads(row[0]) if row[0] else {}
            task = config_data.get("model", "")
            # Try to get task from run_start event
            events = conn.execute(
                "SELECT payload FROM events WHERE run_id=? AND type='run_start' ORDER BY rowid LIMIT 1",
                (self._run_id,),
            ).fetchone()
            task = ""
            workdir = ""
            if events:
                ev = json.loads(events[0])
                task = ev.get("task", "")
                workdir = ev.get("workdir", "")
            # Count turns from events
            turn_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE run_id=? AND type='turn_start'",
                (self._run_id,),
            ).fetchone()
            turns = turn_count[0] if turn_count else 0
            # Get last llm_response usage
            usage = conn.execute(
                "SELECT payload FROM events WHERE run_id=? AND type='llm_response' ORDER BY rowid DESC LIMIT 1",
                (self._run_id,),
            ).fetchone()
            tokens = ""
            if usage:
                u = json.loads(usage[0]).get("usage", {})
                inp = u.get("input_tokens", 0)
                out = u.get("output_tokens", 0)
                tokens = f"In: {inp:,}  Out: {out:,}"
            conn.close()
            return {
                "task": task[:60] if task else "(no task)",
                "workdir": workdir or ".", "status": row[1],
                "turns": turns, "tokens": tokens,
            }
        except Exception:
            return {"status": "error"}

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sd-resume":
            self.dismiss("resume")
        elif event.button.id == "sd-delete":
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute("DELETE FROM events WHERE run_id=?", (self._run_id,))
                conn.execute("DELETE FROM runs WHERE run_id=?", (self._run_id,))
                conn.commit()
                conn.close()
                self.dismiss("deleted")
            except Exception:
                self.dismiss("error")
        elif event.button.id == "sd-back":
            self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key in ("escape",):
            self.dismiss(None)

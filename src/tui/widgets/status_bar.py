from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


_RUN_ICONS = {
    "idle": "  ",
    "running": "\u25cf ",
    "done": "\u2713 ",
    "error": "\u2717 ",
}


class StatusBar(Static):
    """Bottom status bar showing run state and metrics."""

    turn_info: reactive[str] = reactive("Turn 0/0")
    token_info: reactive[str] = reactive("Tokens: 0/0")
    mode_info: reactive[str] = reactive("Mode: normal")
    run_state: reactive[str] = reactive("idle")
    compression_info: reactive[str] = reactive("")

    def watch_turn_info(self, val: str) -> None:
        self._update()

    def watch_token_info(self, val: str) -> None:
        self._update()

    def watch_mode_info(self, val: str) -> None:
        self._update()

    def watch_run_state(self, val: str) -> None:
        self._update()

    def watch_compression_info(self, val: str) -> None:
        self._update()

    def _update(self) -> None:
        icon = _RUN_ICONS.get(self.run_state, "  ")
        parts = [
            f"{icon}{self.turn_info}",
            self.token_info,
            self.mode_info,
        ]
        if self.compression_info:
            parts.append(self.compression_info)
        self.update(" │ ".join(p for p in parts if p))

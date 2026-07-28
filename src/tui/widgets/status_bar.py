from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static


class StatusBar(Static):
    """Bottom status bar showing run state."""

    turn_info: reactive[str] = reactive("Turn 0/0")
    token_info: reactive[str] = reactive("Tokens: 0/0")
    mode_info: reactive[str] = reactive("Mode: normal")
    model_info: reactive[str] = reactive("")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def watch_turn_info(self, val: str) -> None:
        self._update()

    def watch_token_info(self, val: str) -> None:
        self._update()

    def watch_mode_info(self, val: str) -> None:
        self._update()

    def watch_model_info(self, val: str) -> None:
        self._update()

    def _update(self) -> None:
        parts = [self.turn_info, self.token_info, self.mode_info, self.model_info]
        self.update(" │ ".join(p for p in parts if p))

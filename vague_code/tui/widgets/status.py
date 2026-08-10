"""Activity line widget showing agent status and turn metrics."""

from __future__ import annotations

from textual.widgets import Static

from vague_code.tui.views.activity import activity_markup, truncate_activity_text, turn_metrics_text


class ActivityLine(Static):
    """Single status line with an animated activity text and right-aligned metrics."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._activity_text = "idle · ready"
        self._metrics_text = ""

    def update_activity(self, text: str) -> None:
        self._activity_text = text
        self._redraw()

    def update_metrics(self, elapsed_seconds: float | None, tool_count: int) -> None:
        if elapsed_seconds is None:
            self._metrics_text = ""
        else:
            self._metrics_text = turn_metrics_text(elapsed_seconds, tool_count)
        self._redraw()

    def _redraw(self) -> None:
        line = self._activity_text
        if self._metrics_text:
            width = max(0, self.size.width - len(self._metrics_text) - 1)
            line = f"{truncate_activity_text(line, width)} {self._metrics_text}"
        self.update(activity_markup(line))

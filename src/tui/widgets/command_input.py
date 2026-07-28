from __future__ import annotations

from textual.widgets import Input


class CommandInput(Input):
    """Input field for user commands and slash commands."""

    BINDINGS = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, placeholder="Type a task or command...", **kwargs)

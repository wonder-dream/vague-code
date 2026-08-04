"""Slash command system for the XClaw TUI.

Ported from the firstcoder TUI reference (app/commands.py, app/router.py):
handlers produce a CommandResult (handled / output / action); the app executes
the action (open picker, submit chat, clear output, ...). Commands are the
single source of truth — pickers re-route selections back through handle().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CommandResult:
    handled: bool = False
    output: str = ""
    action: dict[str, Any] | None = None


class CommandHandler:
    name = "base"

    def handle(self, text: str) -> CommandResult:
        return CommandResult()

    def _match(self, text: str, command: str) -> bool:
        parts = text.strip().split()
        return bool(parts) and parts[0].lower() == command


class CompositeCommandHandler:
    """Dispatch a slash command to the first handler that claims it."""

    def __init__(self, handlers: list[CommandHandler] | None = None) -> None:
        self._handlers: list[CommandHandler] = list(handlers or [])

    def register(self, handler: CommandHandler) -> None:
        self._handlers.append(handler)

    def handle(self, text: str) -> CommandResult:
        for handler in self._handlers:
            result = handler.handle(text)
            if result.handled:
                return result
        return CommandResult()


def picker_command(kind: str, item) -> str:
    """Turn a picked item back into the slash command that selects it."""
    item_id = str(getattr(item, "id", "") or "")
    if kind == "resume":
        return f"/resume {item_id}"
    if kind == "model":
        return f"/model {item_id}"
    return f"/resume {item_id}"

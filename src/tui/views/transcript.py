"""Transcript entry classification and CSS helpers for the XClaw TUI.

Ported from the firstcoder TUI reference (app/transcript_view.py).
"""

from __future__ import annotations

from src.tui.state import TuiEntryKind, TuiTranscriptEntry


def entry_classes(entry: TuiTranscriptEntry) -> str:
    base = "message"
    if entry.kind == TuiEntryKind.SYSTEM:
        return f"{base} system-message"
    if entry.kind == TuiEntryKind.COMMAND:
        return f"{base} command-message"
    if entry.kind == TuiEntryKind.USER:
        return f"{base} user-message"
    if entry.kind == TuiEntryKind.ASSISTANT:
        return f"{base} assistant-message"
    if entry.kind == TuiEntryKind.REASONING:
        return f"{base} reasoning-message"
    if entry.kind == TuiEntryKind.PERMISSION:
        if entry.status == "permission_requested":
            return f"{base} permission-message permission-requested"
        return f"{base} permission-message"
    if entry.kind == TuiEntryKind.ERROR:
        return f"{base} error-message"
    if entry.kind == TuiEntryKind.TOOL:
        if entry.status == "running":
            return f"{base} tool-message tool-running"
        if entry.status == "success":
            return f"{base} tool-message tool-done"
        if entry.status in {"error", "denied", "failed"}:
            return f"{base} tool-message tool-failed"
        if entry.status == "skipped":
            return f"{base} tool-message tool-skipped"
        return f"{base} tool-message"
    return f"{base} system-message"


def entry_plain_text(entry: TuiTranscriptEntry) -> str:
    if entry.kind in {TuiEntryKind.USER, TuiEntryKind.ASSISTANT, TuiEntryKind.TOOL, TuiEntryKind.REASONING}:
        return f"{entry.label}\n  {entry.body}"
    return entry.body


def entry_markdown_text(entry: TuiTranscriptEntry) -> str:
    return f"{entry.label}\n\n{entry.body}"

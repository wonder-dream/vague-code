"""App-level streaming tests: drive XClawApp events with a stub agent worker."""

import tempfile
from pathlib import Path

from src.agent.config import AgentConfig
from src.agent.ir import (
    ArgsDelta,
    MessageEnd,
    MessageStart,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolUseEnd,
    ToolUseStart,
)
from src.tui.app import XClawApp
from src.tui.state import TuiEntryKind


class _FakeBackend:
    name = "fake"


class _FakeTrajectory:
    run_id = "fake-run"
    events = []


class _StreamTestApp(XClawApp):
    def __init__(self, *args, events=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fake_events = events or []
        self._trajectory_events = []

    def _run_agent_worker(self, state, text: str, token: int) -> None:
        for ev in self._fake_events:
            self.call_from_thread(self._on_stream_event, ev, state, token)
        self.call_from_thread(self._on_run_complete, _FakeTrajectory(), state, token)


def _make_app(**kwargs) -> _StreamTestApp:
    config = AgentConfig(model="test-model", max_turns=2, db_path=str(Path(tempfile.mkdtemp()) / "runs.db"))
    config.permission_mode = "normal"
    return _StreamTestApp(config=config, backend=_FakeBackend(), workdir=".", **kwargs)


async def test_stream_text_renders_markdown_and_finalizes() -> None:
    app = _make_app(events=[
        MessageStart(model="m"),
        TextDelta(delta="## hello\n"),
        TextDelta(delta="world"),
        MessageEnd(stop_reason=StopReason.end_turn),
    ])
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause(0.4)
        markdown = app.query_one("XClawMarkdown")
        assert "world" in markdown.source
        assert markdown.allow_select is True
        assistant_entries = [e for e in app.transcript.entries if e.kind == TuiEntryKind.ASSISTANT]
        assert assistant_entries and "world" in assistant_entries[-1].body


async def test_thinking_rendered_as_reasoning_entry() -> None:
    app = _make_app(events=[
        MessageStart(model="m"),
        ThinkingStart(),
        ThinkingDelta(delta="step one"),
        ThinkingDelta(delta=", step two"),
        ThinkingEnd(signature=None),
        MessageEnd(stop_reason=StopReason.end_turn),
    ])
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause(0.4)
        reasoning = [e for e in app.transcript.entries if e.kind == TuiEntryKind.REASONING]
        assert reasoning and reasoning[-1].body == "step one, step two"


async def test_empty_thinking_removed() -> None:
    app = _make_app(events=[
        ThinkingStart(),
        ThinkingEnd(signature=None),
        TextDelta(delta="answer"),
        MessageEnd(stop_reason=StopReason.end_turn),
    ])
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause(0.4)
        kinds = [e.kind for e in app.transcript.entries]
        assert TuiEntryKind.REASONING not in kinds


async def test_tool_call_and_result_update_entry() -> None:
    app = _make_app(events=[
        MessageStart(model="m"),
        ToolUseStart(id="t1", name="bash"),
        ArgsDelta(id="t1", delta='{"command": "ls"}'),
        ToolUseEnd(id="t1"),
        TextDelta(delta="files listed"),
        MessageEnd(stop_reason=StopReason.end_turn),
    ])
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("run")
        await pilot.pause()
        state = app._sessions.current
        app._on_tool_result("t1", "bash", "ok output", False, state, state.active_token)
        await pilot.pause(0.4)
        tool_entries = [e for e in app.transcript.entries if e.kind == TuiEntryKind.TOOL]
        assert tool_entries and tool_entries[0].status == "success"
        assert "ok output" in tool_entries[0].body


async def test_stale_events_dropped_after_interrupt() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session("t")
        token = app._begin_session_turn(state)
        app._interrupt_chat_turn()
        app._on_stream_event(TextDelta(delta="stale"), state, token)
        await pilot.pause(0.4)
        assert not any(e.kind == TuiEntryKind.ASSISTANT for e in app.transcript.entries)


async def test_task_autostart_on_mount() -> None:
    app = _make_app(task="auto task", events=[TextDelta(delta="result"), MessageEnd(stop_reason=StopReason.end_turn)])
    async with app.run_test() as pilot:
        await pilot.pause(0.4)
        assert any(e.kind == TuiEntryKind.USER and e.body == "auto task" for e in app.transcript.entries)



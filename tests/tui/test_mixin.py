"""Mixin activity-animation, turn-metrics, and tool-activity tests."""

import time
from pathlib import Path

from src.agent.config import AgentConfig
from src.agent.ir import (
    ArgsDelta,
    MessageEnd,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ThinkingStart,
    ToolUseStart,
)
from src.tui.app import XClawApp
from src.tui.state import TuiEntryKind
from src.tui.views.activity import tool_activity_line_text, turn_metrics_text

_TUI_THEME = str(Path(__file__).resolve().parents[2] / "src" / "tui" / "theme.tcss")


class _FakeBackend:
    name = "fake"


class _FakeTrajectory:
    events = []


class _TestApp(XClawApp):
    CSS_PATH = _TUI_THEME

    def __init__(self, *args, events=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fake_events = events or []

    def _run_agent_worker(self, text: str, token: int) -> None:
        for ev in self._fake_events:
            self.call_from_thread(self._on_stream_event, ev, token)
        self.call_from_thread(self._on_run_complete, _FakeTrajectory(), token)


def _make_app(**kwargs) -> _TestApp:
    config = AgentConfig(model="m", max_turns=2, db_path="runs/runs.db")
    config.permission_mode = "normal"
    return _TestApp(config=config, backend=_FakeBackend(), workdir=".", **kwargs)


def test_working_indicator_frames() -> None:
    app = _make_app()
    app._show_working_indicator("planning next step...")
    assert app._activity_text.startswith("thinking [")
    app._working_frame_index = 3
    assert app._working_indicator_body() == "thinking [ ..] planning next step..."
    app._stop_working_animation()


def test_complete_working_indicator_switches_to_streaming() -> None:
    app = _make_app()
    app._show_working_indicator("thinking about it")
    app._complete_working_indicator()
    assert app._activity_animation_kind == "streaming"


def test_activity_animation_body_static_and_frames() -> None:
    app = _make_app()
    app._show_static_activity("error · bash")
    assert app._activity_text == "error · bash"
    app._show_activity_animation("running", "bash")
    assert app._activity_text == "running [=   ] · bash"
    app._activity_frame_index = 2
    assert "running [=== ] · bash" == app._activity_animation_body()


def test_turn_metrics_lifecycle() -> None:
    app = _make_app()
    app._start_turn_metrics()
    start = app._turn_started_at
    assert start > 0
    time.sleep(0.05)
    assert app._turn_elapsed_seconds() >= 0.04
    app._finish_turn_metrics()
    assert app._turn_started_at == 0.0
    assert app._turn_elapsed_seconds() == 0.0
    assert turn_metrics_text(0.0, 2) == "0.0s · 2 tools"


def test_record_tool_activity_state_machine() -> None:
    app = _make_app()
    app._start_turn_metrics()
    app._record_tool_activity("bash", "running")
    assert app.transcript.active_tool is not None
    assert app.transcript.active_tool.name == "bash"
    assert app._activity_animation_kind == "running"
    app._record_tool_activity("bash", "success", summary="ok")
    assert app.transcript.active_tool is None
    assert app.transcript.recent_tools[-1].status == "success"
    assert app._activity_animation_kind == "streaming" or app._activity_text.startswith("thinking")
    app._record_tool_activity("bash", "error")
    assert app._activity_text == "error · bash"


def test_running_tools_activity_detail_parallel() -> None:
    app = _make_app()
    app._running_tool_call_ids = {"a", "b"}
    assert app._running_tools_activity_detail("bash") == "2 tools running"
    app._running_tool_call_ids = {"a"}
    assert app._running_tools_activity_detail("bash") == "bash"


def test_tool_activity_line_text_helpers() -> None:
    assert tool_activity_line_text("bash", "running") == "running · bash"
    assert tool_activity_line_text("bash", "success") == "reading bash result"
    assert tool_activity_line_text("bash", "error") == "error · bash"


async def test_working_animation_stops_on_run_complete() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        token = app._begin_chat_turn("t")
        app._on_stream_event(ThinkingStart(), token)
        app._on_stream_event(ThinkingDelta(delta="planning"), token)
        await pilot.pause(0.1)
        assert app._working_timer is not None
        assert app._activity_text.startswith("thinking")
        app._on_run_complete(_FakeTrajectory(), token)
        await pilot.pause(0.4)
        assert app._activity_text.startswith("done")
        assert app._working_timer is None
        await pilot.pause(0.4)
        assert app._activity_text.startswith("done")


async def test_working_animation_stops_on_agent_error() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        token = app._begin_chat_turn("t")
        app._on_stream_event(ThinkingDelta(delta="planning"), token)
        await pilot.pause(0.1)
        app._on_agent_error("boom", token)
        await pilot.pause(0.4)
        assert app._activity_text.startswith("error")
        assert app._working_timer is None


async def test_reset_stream_state_stops_timers() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        token = app._begin_chat_turn("t")
        app._on_stream_event(ThinkingDelta(delta="planning"), token)
        await pilot.pause(0.1)
        timer = app._working_timer
        assert timer is not None
        app._reset_stream_state()
        assert app._working_timer is None
        assert app._activity_animation_kind == ""
        frozen = app._activity_text
        await pilot.pause(0.4)
        assert app._activity_text == frozen


async def test_full_tool_flow_updates_activity_and_transcript() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        token = app._begin_chat_turn("run")
        app._on_stream_event(ThinkingStart(), token)
        app._on_stream_event(ThinkingDelta(delta="plan"), token)
        app._on_stream_event(ToolUseStart(id="t1", name="bash"), token)
        app._on_stream_event(ArgsDelta(id="t1", delta='{"command": "ls"}'), token)
        await pilot.pause()
        assert app._activity_animation_kind == "running"
        assert app._activity_timer is not None
        app._on_tool_result("t1", "bash", "ok", False, token)
        await pilot.pause()
        assert app._activity_text.startswith("thinking")
        app._on_stream_event(TextDelta(delta="done."), token)
        app._on_stream_event(MessageEnd(stop_reason=StopReason.end_turn), token)
        await pilot.pause(0.4)
        tool_entry = [e for e in app.transcript.entries if e.kind == TuiEntryKind.TOOL]
        assert tool_entry and tool_entry[0].status == "success"
        assert app.transcript.recent_tools[-1].name == "bash"
        app._on_run_complete(_FakeTrajectory(), token)

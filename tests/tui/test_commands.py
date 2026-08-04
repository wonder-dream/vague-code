"""Command routing, picker, input history, Esc interrupt, and guidance tests."""

from pathlib import Path


from src.agent.config import AgentConfig
from src.agent.ir import (
    MessageEnd,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
)
from src.tui.app import XClawApp
from src.tui.commands.core import CompositeCommandHandler
from src.tui.picker import (
    TuiPickerItem,
    TuiPickerState,
    render_picker,
    visible_picker_window,
)

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
    kwargs.setdefault("workdir", ".")
    return _TestApp(config=config, backend=_FakeBackend(), **kwargs)


# ── commands ─────────────────────────────────────────────────────────────────

def test_unknown_command_rejected() -> None:
    app = _make_app()
    result = app._command_handler.handle("/nope")
    assert not result.handled


def test_help_command() -> None:
    app = _make_app()
    result = app._command_handler.handle("/help")
    assert result.handled
    assert "/resume" in result.output


def test_model_command_direct_set() -> None:
    app = _make_app()
    result = app._command_handler.handle("/model deepseek-v4-pro")
    assert result.handled
    assert result.action == {"type": "model_changed", "provider": "deepseek", "model": "deepseek-v4-pro"}


def test_model_command_opens_picker_without_arg() -> None:
    app = _make_app()
    result = app._command_handler.handle("/model")
    assert result.handled
    assert result.action["type"] == "open_picker"
    assert len(result.action["items"]) >= 2


def test_mode_command() -> None:
    app = _make_app()
    result = app._command_handler.handle("/mode auto")
    assert result.handled
    assert app._config.permission_mode == "auto"
    bad = app._command_handler.handle("/mode nope")
    assert bad.handled
    assert "Unknown mode" in bad.output


def test_permissions_command_empty(tmp_path) -> None:
    app = _make_app(workdir=str(tmp_path))
    result = app._command_handler.handle("/permissions")
    assert result.handled
    assert "No persistent" in result.output


def test_save_without_trajectory() -> None:
    app = _make_app()
    result = app._command_handler.handle("/save")
    assert result.handled
    assert "No trajectory" in result.output


def test_composite_handler_first_match_wins() -> None:
    class First(CompositeCommandHandler):
        pass

    app = _make_app()
    handler = app._command_handler
    assert handler.handle("/clear").handled
    assert handler.handle("/quit").handled is False


# ── picker ───────────────────────────────────────────────────────────────────

def test_picker_window() -> None:
    items = [TuiPickerItem(id=str(i), label=str(i)) for i in range(10)]
    start, visible = visible_picker_window(items, selected_index=9, limit=4)
    assert start == 6
    assert [i.id for i in visible] == ["6", "7", "8", "9"]


def test_render_picker_selected_marker() -> None:
    picker = TuiPickerState(
        kind="model",
        title="Select a model:",
        items=[TuiPickerItem(id="a", label="A"), TuiPickerItem(id="b", label="B")],
    )
    rendered = render_picker(picker, limit=8)
    assert "> 1. A" in rendered
    assert " 2. B" in rendered


async def test_picker_open_and_number_select() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._handle_slash("/model")
        assert app._picker is not None
        app._submit_task("1")
        await pilot.pause()
        assert app._config.model in ("deepseek-v4-flash",)
        assert app._picker is None


# ── input history / escape ───────────────────────────────────────────────────

def test_input_history_recall() -> None:
    app = _make_app()
    app._record_input_history("first")
    app._record_input_history("second")
    assert app._recall_input_history("up") == "second"
    assert app._recall_input_history("up") == "first"
    assert app._recall_input_history("down") == "second"
    assert app._recall_input_history("down") == ""


def test_escape_interrupt_requires_two_presses() -> None:
    app = _make_app()
    token = app._begin_chat_turn("t")
    assert app._chat_busy is True
    assert app._handle_escape_interrupt() is True  # first press: hint only
    assert app._chat_busy is True
    assert app._is_current_chat_turn(token) is True
    assert app._handle_escape_interrupt() is True  # second press: interrupt
    assert app._chat_busy is False
    assert app._is_current_chat_turn(token) is False


def test_escape_focuses_input_when_idle() -> None:
    app = _make_app()
    assert app._handle_escape_interrupt() is False


# ── guidance ─────────────────────────────────────────────────────────────────

def test_guidance_queue_drain() -> None:
    app = _make_app()
    app._add_guidance("please continue")
    app._add_guidance("focus on tests")
    assert app._drain_guidance() == ["please continue", "focus on tests"]
    assert app._drain_guidance() == []


async def test_submit_while_busy_queues_guidance() -> None:
    app = _make_app(events=[
        TextDelta(delta="partial"),
        MessageEnd(stop_reason=StopReason.end_turn),
    ])
    async with app.run_test() as pilot:
        await pilot.pause()
        app._submit_task("first task")
        await pilot.pause(0.1)
        app._submit_task("guidance note")
        await pilot.pause()
        assert app._has_pending_guidance() or "guidance note" in [e.body for e in app.transcript.entries]
        await pilot.pause(0.5)


# ── thinking fold ────────────────────────────────────────────────────────────

async def test_thinking_folds_long_content_and_toggles() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        token = app._begin_chat_turn("t")
        app._on_stream_event(ThinkingStart(), token)
        app._on_stream_event(ThinkingDelta(delta="word " * 100), token)
        app._on_stream_event(ThinkingEnd(signature=None), token)
        app._on_stream_event(TextDelta(delta="answer"), token)
        app._on_stream_event(MessageEnd(stop_reason=StopReason.end_turn), token)
        await pilot.pause(0.4)
        reasoning = [e for e in app.transcript.entries if e.kind.value == "reasoning"]
        assert reasoning and reasoning[0].status == "folded"
        assert "按 T 展开" in reasoning[0].body
        app.action_toggle_thinking()
        assert reasoning[0].status is None
        assert reasoning[0].body.startswith("word")
        app.action_toggle_thinking()
        assert reasoning[0].status == "folded"

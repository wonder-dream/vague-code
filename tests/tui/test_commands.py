"""Command routing, picker, input history, Esc interrupt, and guidance tests."""

import tempfile
from pathlib import Path

from vague_code.agent.config import AgentConfig
from vague_code.agent.ir import (
    MessageEnd,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
)
from vague_code.tui.app import VagueCodeApp
from vague_code.tui.commands.core import CompositeCommandHandler
from vague_code.tui.picker import (
    TuiPickerItem,
    TuiPickerState,
    render_picker,
    visible_picker_window,
)


class _FakeBackend:
    name = "fake"


class _FakeTrajectory:
    run_id = "fake-run"
    events = []


class _TestApp(VagueCodeApp):
    def __init__(self, *args, events=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fake_events = events or []

    def _run_agent_worker(self, state, text: str, token: int) -> None:
        for ev in self._fake_events:
            self.call_from_thread(self._on_stream_event, ev, state, token)
        self.call_from_thread(self._on_run_complete, _FakeTrajectory(), state, token)


def _make_app(**kwargs) -> _TestApp:
    config = AgentConfig(model="m", max_turns=2, db_path=str(Path(tempfile.mkdtemp()) / "runs.db"))
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
    assert "/quit" not in result.output
    assert "exit" in result.output


def test_filter_commands_prefix() -> None:
    """ADR-0038：命令候选前缀过滤。"""
    from vague_code.tui.commands.handlers import filter_commands

    assert filter_commands("") == []
    assert filter_commands("hello") == []
    names = [c for c, _, _ in filter_commands("/")]
    assert set(names) == {"/help", "/new", "/clear", "/resume", "/compact",
                          "/save", "/model", "/mode", "/permissions"}
    names = [c for c, _, _ in filter_commands("/mo")]
    assert names == ["/model", "/mode"]
    names = [c for c, _, _ in filter_commands("/MOD")]
    assert names == ["/model", "/mode"]
    assert filter_commands("/nope") == []
    # 已带参数（命令后空格）→ 收起候选（ADR-0038）
    assert filter_commands("/model gpt-5.6") == []
    assert filter_commands("/model ") == []


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


def test_model_list_shows_all_providers() -> None:
    """ADR-0039：/model picker 列出全部 provider 的模型（detail 标注服务商）。"""
    app = _make_app()
    app._provider = "openai"
    result = app._command_handler.handle("/model")
    labels = [i["label"] for i in result.action["items"]]
    details = [i["detail"] for i in result.action["items"]]
    assert "gpt-5.6-sol" in labels
    assert "deepseek-v4-flash" in labels
    assert "claude-fable-5" in labels
    assert details.count("openai") == 3
    assert details.count("deepseek") == 2
    direct = app._command_handler.handle("/model gpt-5.6-sol")
    assert direct.action == {"type": "model_changed", "provider": "openai", "model": "gpt-5.6-sol"}
    direct2 = app._command_handler.handle("/model claude-opus-5")
    assert direct2.action == {"type": "model_changed", "provider": "anthropic", "model": "claude-opus-5"}


def test_model_list_for_custom_provider_from_config() -> None:
    app = _make_app()
    app._provider = "fox"
    app._file_config = {
        "providers": {
            "fox": {
                "baseUrl": "https://relay.example.com/v1",
                "apiKeyEnv": "RELAY_KEY",
                "models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            }
        }
    }
    result = app._command_handler.handle("/model")
    labels = [i["label"] for i in result.action["items"]]
    assert "gpt-5.6-sol" in labels
    assert "gpt-5.6-terra" in labels
    assert "deepseek-v4-flash" in labels  # 内置目录模型也列出
    # 配置 models 优先：gpt-5.6-sol 归属 fox 而非 openai
    direct = app._command_handler.handle("/model gpt-5.6-sol")
    assert direct.action["provider"] == "fox"


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
        # 无会话 → 作用于 app 默认；首项 = 排序第一的 anthropic 模型
        assert app._config.model == "claude-fable-5"
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


async def test_escape_interrupt_requires_two_presses() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        token = app._begin_session_turn(state)
        assert state.busy is True
        assert app._handle_escape_interrupt() is True  # first press: hint only
        assert state.busy is True
        assert state.active_token == token
        assert app._handle_escape_interrupt() is True  # second press: interrupt
        assert state.busy is False
        assert state.active_token is None


def test_escape_focuses_input_when_idle() -> None:
    app = _make_app()
    assert app._handle_escape_interrupt() is False


# ── guidance ─────────────────────────────────────────────────────────────────

async def test_guidance_queue_drain() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        app._add_guidance(state, "please continue")
        app._add_guidance(state, "focus on tests")
        assert app._drain_guidance(state) == ["please continue", "focus on tests"]
        assert app._drain_guidance(state) == []


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
        assert "guidance note" in [e.body for e in app.transcript.entries]
        await pilot.pause(0.5)


async def test_suggest_popup_on_slash() -> None:
    """ADR-0038：键入 / 弹出命令候选浮层，/mo 过滤，Enter 无参执行、有参填入。"""
    from vague_code.tui.widgets.command_suggest import CommandSuggest

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.query_one("#input")
        suggest = app.query_one("#command-suggest", CommandSuggest)
        assert not suggest.is_visible()

        # 键入 "/" → 浮层出现，列出全部命令
        input_widget.load_text("/")
        await pilot.pause(0.1)
        assert suggest.is_visible()
        assert len(suggest._items) == 9

        # "/mo" → 过滤为 /model /mode
        input_widget.load_text("/mo")
        await pilot.pause(0.1)
        names = [c for c, _, _ in suggest._items]
        assert names == ["/model", "/mode"]

        # Esc 关闭
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not suggest.is_visible()

        # Enter 选无参命令 /new 直接执行（新会话）
        input_widget.load_text("/new")
        await pilot.pause(0.1)
        assert suggest.is_visible()
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert input_widget.text == ""
        assert app._sessions.current is not None

        # Enter 选有参命令 /model → 填入 "/model " 继续输参数
        input_widget.load_text("/model")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert input_widget.text == "/model "
        assert not suggest.is_visible()

        # 非 "/" 输入隐藏浮层
        input_widget.load_text("hello")
        await pilot.pause(0.1)
        assert not suggest.is_visible()


async def test_suggest_scrolls_window_keeps_selection_visible() -> None:
    """ADR-0038 修复：9 个命令超出可视行时滚动窗口，选中项始终可见。"""
    from vague_code.tui.widgets.command_suggest import CommandSuggest

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.query_one("#input")
        suggest = app.query_one("#command-suggest", CommandSuggest)

        input_widget.load_text("/")
        await pilot.pause(0.1)
        assert len(suggest._items) == 9

        # 初始：窗口从第 0 项开始
        start, end = suggest._visible_window()
        assert (start, end) == (0, 6)

        # 向下移到第 7 项（index 7，超出首屏）→ 窗口滚动，选中项在窗口内
        for _ in range(7):
            suggest.move(1)
        assert suggest.selected_index == 7
        start, end = suggest._visible_window()
        assert start == 2 and end == 8, f"窗口应滚动到 [2,8)，实际 [{start},{end})"
        assert start <= suggest.selected_index < end

        # 渲染的行数不超可视行，且选中行高亮
        visible_rows = [r for r in suggest._rows if r.display]
        assert len(visible_rows) == 6
        highlighted = [r for r in visible_rows if "suggest-selected" in r.classes]
        assert len(highlighted) == 1

        # 移到最底部（index 8）窗口停在末尾
        suggest.move(1)
        assert suggest.selected_index == 8
        start, end = suggest._visible_window()
        assert (start, end) == (3, 9)

        # 上移回 index 0 → 窗口回顶部
        for _ in range(8):
            suggest.move(-1)
        assert suggest.selected_index == 0
        assert suggest._visible_window() == (0, 6)


async def test_suggest_hides_when_focus_leaves_input() -> None:
    """焦点切走输入框 → 命令浮层收起（否则永远覆盖输入栏）。"""
    from vague_code.tui.widgets.command_suggest import CommandSuggest

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        input_widget = app.query_one("#input")
        suggest = app.query_one("#command-suggest", CommandSuggest)

        input_widget.load_text("/")
        await pilot.pause(0.1)
        assert suggest.is_visible()

        # 焦点移到侧边栏（先展开使其可聚焦）→ 浮层收起
        app.action_toggle_sidebar()
        await pilot.pause(0.1)
        sidebar = app.query_one("#sidebar")
        sidebar.focus()
        await pilot.pause(0.1)
        assert not suggest.is_visible()

        # 焦点回到输入框 → 浮层恢复
        input_widget.focus()
        await pilot.pause(0.1)
        assert suggest.is_visible()


# ── thinking fold ────────────────────────────────────────────────────────────

async def test_thinking_folds_long_content_and_toggles() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        token = app._begin_session_turn(state)
        app._on_stream_event(ThinkingStart(), state, token)
        app._on_stream_event(ThinkingDelta(delta="word " * 100), state, token)
        app._on_stream_event(ThinkingEnd(signature=None), state, token)
        app._on_stream_event(TextDelta(delta="answer"), state, token)
        app._on_stream_event(MessageEnd(stop_reason=StopReason.end_turn), state, token)
        await pilot.pause(0.4)
        reasoning = [e for e in app.transcript.entries if e.kind.value == "reasoning"]
        assert reasoning and reasoning[0].status == "folded"
        assert "按 T 展开" in reasoning[0].body
        app.action_toggle_thinking()
        assert reasoning[0].status is None
        assert reasoning[0].body.startswith("word")
        app.action_toggle_thinking()
        assert reasoning[0].status == "folded"


# ── /compact ─────────────────────────────────────────────────────────────────

def test_compact_command_routes_to_action() -> None:
    app = _make_app()
    result = app._command_handler.handle("/compact")
    assert result.handled
    assert result.action == {"type": "compact_session"}


async def test_compact_without_session_prompts() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._handle_slash("/compact")
        await pilot.pause()
        assert any(
            e.body == "当前没有活动会话。"
            for e in app.transcript.entries
        )


async def test_compact_busy_session_rejected(monkeypatch) -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        app._begin_session_turn(state)
        assert state.busy is True
        app._handle_slash("/compact")
        await pilot.pause()
        assert any(
            "运行中" in e.body
            for e in app.transcript.entries
        )


async def test_compact_reclaims_tokens(monkeypatch) -> None:
    class _CompactAgent:
        def compact_chat(self):
            return {"before": 50_000, "after": 5_000, "affected": 6}

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        state.agent = _CompactAgent()  # type: ignore[assignment]
        app._handle_slash("/compact")
        await pilot.pause(0.3)
        assert any(
            "已压缩：回收 43.9k tokens" in e.body
            for e in app.transcript.entries
        )


async def test_compact_failure_reports_error(monkeypatch) -> None:
    class _BoomAgent:
        def compact_chat(self):
            raise RuntimeError("boom")

    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        state = app._begin_new_session()
        state.agent = _BoomAgent()  # type: ignore[assignment]
        app._handle_slash("/compact")
        await pilot.pause(0.3)
        assert any(
            "压缩失败" in e.body
            for e in app.transcript.entries
        )



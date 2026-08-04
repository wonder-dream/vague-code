"""Composer key handling and exit/copy behavior tests."""

from pathlib import Path

from src.agent.config import AgentConfig
from src.tui.app import XClawApp
from src.tui.widgets.common import ComposerTextArea

_TUI_THEME = str(Path(__file__).resolve().parents[2] / "src" / "tui" / "theme.tcss")


class _FakeBackend:
    name = "fake"


class _TestApp(XClawApp):
    CSS_PATH = _TUI_THEME


def _make_app(**kwargs) -> _TestApp:
    config = AgentConfig(model="m", max_turns=2, db_path="runs/runs.db")
    config.permission_mode = "normal"
    return _TestApp(config=config, backend=_FakeBackend(), workdir=".", **kwargs)


async def test_shift_enter_inserts_newline() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#input", ComposerTextArea)
        composer.focus()
        composer.text = "hello"
        composer.cursor_location = composer.document.end
        await pilot.press("shift+enter")
        await pilot.pause()
        assert "\n" in composer.text
        assert not app.transcript.entries  # not submitted


async def test_ctrl_j_inserts_newline() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#input", ComposerTextArea)
        composer.focus()
        composer.text = "hello"
        composer.cursor_location = composer.document.end
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert "\n" in composer.text
        assert not app.transcript.entries


async def test_enter_submits() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#input", ComposerTextArea)
        composer.focus()
        composer.text = "hello"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert any(e.body == "hello" for e in app.transcript.entries)


async def test_exit_word_quits() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#input", ComposerTextArea)
        composer.focus()
        composer.text = "exit"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert app.return_value == "User quit"
        assert not any(e.body == "exit" for e in app.transcript.entries)


async def test_ctrl_c_idle_does_not_quit() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.action_copy_or_interrupt()
        await pilot.pause(0.1)
        assert app.return_value is None
        assert any(
            e.body.startswith("无选中文本") for e in app.transcript.entries
        )


async def test_ctrl_c_with_selection_copies_textarea() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        composer = app.query_one("#input", ComposerTextArea)
        composer.focus()
        await pilot.pause()
        composer.text = "select me"
        composer.cursor_location = (0, 9)
        composer.selection = ((0, 0), (0, 5))
        copied = []
        composer.action_copy = lambda: copied.append(composer.selected_text)
        await app.action_copy_or_interrupt()
        await pilot.pause(0.1)
        assert copied == ["selec"]
        assert app.return_value is None

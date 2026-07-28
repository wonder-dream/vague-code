from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from src.tui.widgets.status_bar import StatusBar


class _TestApp(App):
    def compose(self) -> ComposeResult:
        yield StatusBar(id="status")


@pytest.fixture
async def app():
    a = _TestApp()
    async with a.run_test(size=(80, 24)) as p:
        yield p.app


class TestStatusBar:
    async def test_default_text(self, app) -> None:
        status = app.query_one("#status", StatusBar)
        rendered = status.render()
        rendered_str = str(rendered) if rendered else ""
        assert "Turn" in rendered_str

    async def test_turn_info_updates(self, app) -> None:
        status = app.query_one("#status", StatusBar)
        status.turn_info = "Turn 5/20"
        assert status.turn_info == "Turn 5/20"

    async def test_token_info_updates(self, app) -> None:
        status = app.query_one("#status", StatusBar)
        status.token_info = "In: 1,234  Out: 567"
        assert status.token_info == "In: 1,234  Out: 567"

    async def test_mode_info_updates(self, app) -> None:
        status = app.query_one("#status", StatusBar)
        status.mode_info = "Mode: auto"
        assert status.mode_info == "Mode: auto"

    async def test_update_called(self, app) -> None:
        """Setting reactive properties calls _update."""
        status = app.query_one("#status", StatusBar)
        status.turn_info = "Turn 10/20"
        status.token_info = "Tok: 5K"
        status.mode_info = "Mode: safe"
        rendered = status.render()
        rendered_str = str(rendered) if rendered else ""
        assert "Turn 10/20" in rendered_str
        assert "Tok: 5K" in rendered_str
        assert "Mode: safe" in rendered_str

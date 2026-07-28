from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from src.agent.permission import Decision, Operation
from src.tui.screens.permission import PermissionDialog


class _TestApp(App):
    def compose(self) -> ComposeResult:
        op = Operation(tool_name="bash", input={"command": "ls"}, command="ls")
        yield PermissionDialog(op)


@pytest.fixture
async def pilot():
    a = _TestApp()
    async with a.run_test(size=(80, 24)) as p:
        yield p


class TestPermissionDialog:
    async def test_button_approve_present(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        btn = dialog.query_one("#perm-allow")
        assert btn is not None

    async def test_always_allow_button_present(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        btn = dialog.query_one("#perm-allow-always")
        assert btn is not None

    async def test_deny_button_present(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        btn = dialog.query_one("#perm-deny")
        assert btn is not None

    async def test_title_shown(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        title = dialog.query_one("#perm-title")
        assert "Permission" in str(title.render())

    async def test_tool_name_shown(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        tool = dialog.query_one("#perm-tool")
        assert "bash" in str(tool.render())

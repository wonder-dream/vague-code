from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vague_code.agent.permission import Decision, Operation
from vague_code.tui.screens.permission import PermissionDialog
from vague_code.tui.views.permission import operation_rows, permission_title, tool_accent


def _dialog_app(op: Operation) -> App:
    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield PermissionDialog(op)

    return _TestApp()


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_operation_rows_bash() -> None:
    op = Operation(tool_name="bash", input={"command": "ls -la"}, command="ls -la")
    rows = operation_rows(op)
    assert rows == [("command", "ls -la")]


def test_operation_rows_write_file() -> None:
    op = Operation(tool_name="write_file", input={"path": "a.py", "content": "x = 1\n" * 100})
    rows = dict(operation_rows(op))
    assert rows["path"] == "a.py"
    assert rows["content"].endswith("...")


def test_operation_rows_patch_and_glob() -> None:
    patch = Operation(tool_name="patch", input={"path": "a.py", "old_str": "old", "new_str": "new"})
    assert dict(operation_rows(patch))["new"] == "new"
    glob = Operation(tool_name="glob", input={"pattern": "**/*.py"})
    assert dict(operation_rows(glob))["pattern"] == "**/*.py"


def test_titles_and_accents() -> None:
    assert permission_title("bash") == "Run command"
    assert permission_title("write_file") == "Write file"
    assert tool_accent("bash") == "#b28443"
    assert tool_accent("write_file") == "#c85f5f"
    assert tool_accent("read_file") == "#50b7c2"


# ── dialog rendering ─────────────────────────────────────────────────────────

@pytest.fixture
async def pilot():
    op = Operation(tool_name="bash", input={"command": "ls"}, command="ls")
    app = _dialog_app(op)
    async with app.run_test(size=(80, 24)) as p:
        yield p


class TestPermissionDialog:
    async def test_buttons_present(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        for button_id in ("perm-allow", "perm-allow-always", "perm-deny"):
            assert dialog.query_one(f"#{button_id}") is not None

    async def test_header_shows_title_and_tool(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        header = dialog.query_one("#perm-header")
        rendered = str(header.render())
        assert "Run command" in rendered
        assert "bash" in rendered

    async def test_body_shows_shell_command(self, pilot) -> None:
        dialog = pilot.app.query_one(PermissionDialog)
        body = dialog.query_one("#perm-body")
        assert "ls" in str(body.render())

    async def test_y_approves(self) -> None:
        op = Operation(tool_name="bash", input={"command": "ls"}, command="ls")
        decision_holder = {}

        class _HostApp(App):
            def on_mount(self) -> None:
                self.push_screen(
                    PermissionDialog(op),
                    callback=lambda decision: decision_holder.__setitem__("decision", decision),
                )

        async with _HostApp().run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert getattr(pilot.app.focused, "id", None) == "perm-allow"
            await pilot.press("y")
            await pilot.pause()
            assert decision_holder["decision"] == Decision.ALLOW

    async def test_deny_with_feedback_sets_operation(self) -> None:
        op = Operation(tool_name="bash", input={"command": "rm x"}, command="rm x")

        class _App(App):
            def compose(self) -> ComposeResult:
                yield PermissionDialog(op)

        async with _App().run_test(size=(80, 24)) as pilot:
            dialog = pilot.app.query_one(PermissionDialog)
            decisions = []
            dialog.dismiss = lambda decision: decisions.append(decision)
            feedback = dialog.query_one("#perm-feedback")
            feedback.text = "不要删文件"
            dialog.on_button_pressed(type("P", (), {"button": type("B", (), {"id": "perm-deny"})()})())
            await pilot.pause()
            assert op.feedback == "不要删文件"
            assert decisions == [Decision.DENY]


class TestPermissionDialogMarkupSafety:
    async def test_markup_breaking_command_renders(self) -> None:
        op = Operation(
            tool_name="bash",
            input={"command": "dd if=/dev/zero of=/dev/sda", "extra": ["[x]"]},
            command="dd if=/dev/zero of=/dev/sda",
        )
        async with _dialog_app(op).run_test(size=(80, 24)) as pilot:
            dialog = pilot.app.query_one(PermissionDialog)
            body = dialog.query_one("#perm-body")
            assert "/dev/zero" in str(body.render())

    async def test_markup_breaking_tool_name_renders(self) -> None:
        op = Operation(tool_name="bash[red]x", input={"command": "ls"}, command="ls")
        async with _dialog_app(op).run_test(size=(80, 24)) as pilot:
            dialog = pilot.app.query_one(PermissionDialog)
            header = dialog.query_one("#perm-header")
            assert "bash" in str(header.render())

    async def test_review_card_present_when_review_attached(self) -> None:
        op = Operation(
            tool_name="write_file",
            input={"path": "a.py", "content": "x"},
            review={
                "files": [{"path": "a.py", "operation": "CREATE", "added_lines": 1, "removed_lines": 0, "diff": "+x"}],
                "summary": {"added_lines": 1, "removed_lines": 0},
            },
        )
        async with _dialog_app(op).run_test(size=(80, 24)) as pilot:
            dialog = pilot.app.query_one(PermissionDialog)
            review = dialog.query_one("#perm-review")
            assert "Review before writing" in str(review.render())

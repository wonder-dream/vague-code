"""Spike tests: VagueCodeMarkdown portability on textual 8.x + ConversationView."""

from textual.app import App, ComposeResult

from vague_code.tui.state import TuiEntryKind, TuiTranscript
from vague_code.tui.widgets import VagueCodeMarkdown, _observe_markdown_update
from vague_code.tui.widgets.conversation import ConversationView


class _MarkdownApp(App):
    def compose(self) -> ComposeResult:
        yield VagueCodeMarkdown("# title", selectable=False, id="md")


class _ConversationApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.transcript = TuiTranscript()

    def compose(self) -> ComposeResult:
        yield ConversationView(self.transcript, id="output")


async def test_vaguecode_markdown_blocks_and_selectability() -> None:
    app = _MarkdownApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        markdown = app.query_one("#md", VagueCodeMarkdown)
        assert markdown.allow_select is False
        assert VagueCodeMarkdown.BLOCKS
        assert all(block.ALLOW_SELECT for block in VagueCodeMarkdown.BLOCKS.values())
        markdown.set_selectable(True)
        assert markdown.allow_select is True


async def test_vaguecode_markdown_update_is_observable() -> None:
    app = _MarkdownApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        markdown = app.query_one("#md", VagueCodeMarkdown)
        update_result = markdown.update("# updated\n\nbody text")
        _observe_markdown_update(update_result)
        await pilot.pause()
        source = markdown.source
        assert source.startswith("# updated")


async def test_conversation_renders_entries_and_keeps_widget_refs() -> None:
    app = _ConversationApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.query_one("#output", ConversationView)
        user = app.transcript.add(TuiEntryKind.USER, "hello")
        assistant = app.transcript.add(TuiEntryKind.ASSISTANT, "## reply")
        conv.add_entry(user)
        conv.add_entry(assistant)
        await pilot.pause()
        assert user.widget is not None
        assert assistant.widget is not None
        assert len(list(conv.query("VagueCodeMarkdown"))) == 1
        assert len(list(conv.query(".user-message"))) == 1


async def test_conversation_clear_resets() -> None:
    app = _ConversationApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        conv = app.query_one("#output", ConversationView)
        entry = app.transcript.add(TuiEntryKind.USER, "hi")
        conv.add_entry(entry)
        await pilot.pause()
        assert conv.transcript.entries
        conv.clear()
        await pilot.pause()
        assert not conv.transcript.entries
        assert not list(conv.query("Static"))

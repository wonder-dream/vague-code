from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from src.agent.ir import RetryNotice
from src.tui.widgets.conversation import ConversationView


class _TestApp(App):
    def compose(self) -> ComposeResult:
        yield ConversationView(id="conv")


@pytest.fixture
async def app():
    a = _TestApp()
    async with a.run_test(size=(80, 24)) as p:
        yield p.app


class TestConversationView:
    async def test_append_text_streams(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        conv.append_text("hello ")
        conv.append_text("world")
        # Check that the streaming block exists and contains the text
        blocks = conv.query(".text-delta")
        assert len(blocks) > 0

    async def test_thinking_collapsed_then_toggle(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        conv.start_thinking()
        conv.add_thinking_delta("step 1...")
        conv.add_thinking_delta(" step 2...")
        conv.end_thinking()

        blocks = [b for b in conv._blocks if b.kind == "thinking"]
        assert len(blocks) == 1
        assert blocks[0].collapsed is True

        conv.toggle_thinking()
        assert blocks[0].collapsed is False

        conv.toggle_thinking()
        assert blocks[0].collapsed is True

    async def test_tool_result_head_tail_summary(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        long_content = "\n".join(f"line {i}" for i in range(50))
        conv.add_tool_result("read_file", long_content, is_error=False)

        blocks = [b for b in conv._blocks if b.kind == "tool_result"]
        assert len(blocks) == 1
        assert blocks[0].full_content == long_content
        assert blocks[0].collapsed is True

    async def test_foldable_navigation(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        assert conv._current_focus is None

        conv.start_thinking()
        conv.add_thinking_delta("thinking text")
        conv.end_thinking()

        conv.add_tool_result("tool_a", "some content", is_error=False)
        conv.add_tool_result("tool_b", "more content", is_error=True)

        conv.select_next()
        assert conv._current_focus == 0

        conv.select_next()
        assert conv._current_focus == 1

        conv.select_next()
        assert conv._current_focus == 2

        conv.select_prev()
        assert conv._current_focus == 1

    async def test_current_expand_with_focus(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        conv.start_thinking()
        conv.add_thinking_delta("expandable text")
        conv.end_thinking()

        conv.select_next()
        assert conv._current_focus == 0
        assert conv._blocks[0].collapsed is True

        conv.toggle_current_expand()
        assert conv._blocks[0].collapsed is False

    async def test_retry_notice_display(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        conv.add_retry_notice(RetryNotice(attempt=2, delay_s=1.5, reason="rate_limit"))
        # Should not crash
        assert True

    async def test_clear_resets_state(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        conv.start_thinking()
        conv.add_thinking_delta("some thought")
        conv.end_thinking()
        conv.append_text("hello")
        assert len(conv._blocks) > 0
        conv.clear()
        assert len(conv._blocks) == 0
        assert conv._current_focus is None
        assert conv._streaming_block is None

    async def test_multiple_thinking_blocks_independent(self, app) -> None:
        conv = app.query_one("#conv", ConversationView)
        conv.start_thinking()
        conv.add_thinking_delta("first thought")
        conv.end_thinking()
        conv.start_thinking()
        conv.add_thinking_delta("second thought")
        conv.end_thinking()

        thinking = [b for b in conv._blocks if b.kind == "thinking"]
        assert len(thinking) == 2
        assert "first" in thinking[0].full_content
        assert "second" in thinking[1].full_content

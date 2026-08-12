"""ModelPicker 独立模型选择界面测试（/model 无参数打开）。"""

from __future__ import annotations

from textual.app import App

from vague_code.tui.screens.model_picker import ModelPicker, VISIBLE_ROWS

MODELS = [
    ("deepseek", "deepseek-v4-flash", True),
    ("deepseek", "deepseek-v4-pro", True),
    ("openai", "gpt-5.6-sol", False),
    ("openai", "gpt-5.6-terra", False),
    ("anthropic", "claude-fable-5", False),
]


def _app() -> App:
    class _TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(ModelPicker(MODELS))

    return _TestApp()


async def _wait_picker(pilot) -> ModelPicker:
    """轮询等 ModelPicker 成为当前 screen（push_screen 异步切换）。"""
    for _ in range(100):
        if isinstance(pilot.app.screen, ModelPicker):
            return pilot.app.screen
        await pilot.pause(0.05)
    raise AssertionError("ModelPicker 未挂载")


async def test_picker_shows_all_models_with_lock_mark() -> None:
    async with _app().run_test(size=(90, 30)) as pilot:
        await _wait_picker(pilot)
        list_text = pilot.app.screen.query_one("#model-list").render()
        rendered = str(list_text)
        assert "deepseek-v4-flash" in rendered
        assert "gpt-5.6-sol" in rendered
        assert "[需配置]" in rendered  # 无 key 模型标记


async def test_search_filters_models() -> None:
    async with _app().run_test(size=(90, 30)) as pilot:
        await _wait_picker(pilot)
        search = pilot.app.screen.query_one("#model-search")
        search.value = "gpt"
        await pilot.pause()
        rendered = str(pilot.app.screen.query_one("#model-list").render())
        assert "gpt-5.6-sol" in rendered
        assert "deepseek-v4-flash" not in rendered
        search.value = "anthropic"
        await pilot.pause()
        rendered = str(pilot.app.screen.query_one("#model-list").render())
        assert "claude-fable-5" in rendered
        assert "gpt-5.6-sol" not in rendered


async def test_enter_confirms_selected_model() -> None:
    results: list = []

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ModelPicker(MODELS), callback=results.append)

    async with _Host().run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert results == [("deepseek", "deepseek-v4-flash")]


async def test_arrows_move_selection() -> None:
    results: list = []

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ModelPicker(MODELS), callback=results.append)

    async with _Host().run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        assert results == [("openai", "gpt-5.6-sol")]


async def test_escape_cancels() -> None:
    results: list = []

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ModelPicker(MODELS), callback=results.append)

    async with _Host().run_test(size=(90, 30)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert results == [None]


async def test_scroll_window_keeps_selection_visible() -> None:
    many = [(f"p{i}", f"model-{i}", True) for i in range(VISIBLE_ROWS + 10)]

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(ModelPicker(many))

    async with _Host().run_test(size=(90, 30)) as pilot:
        await _wait_picker(pilot)
        picker = pilot.app.screen
        for _ in range(VISIBLE_ROWS + 5):
            picker._selected += 1
        picker._refresh_list()
        rendered = str(pilot.app.screen.query_one("#model-list").render())
        assert f"model-{VISIBLE_ROWS + 5}" in rendered  # 选中项在窗口内
        assert picker._selected < len(many)

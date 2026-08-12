"""独立模型选择界面（ModelPicker，对齐 opencode 模型选择面板）。

`/model`（无参数）打开：搜索过滤 + 滚动列表 + ↑/↓ 选择 + Enter 确认 + Esc 取消。
无 key 的模型显示 [需配置] 标记；确认后由 app 决定直切 / 换 backend / 弹引导。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

MODEL_ACCENTS = {
    "deepseek": "#7bba55",
    "openai": "#50b7c2",
    "anthropic": "#d9a05b",
    "custom": "#9aa0a6",
}

VISIBLE_ROWS = 12


class ModelPicker(ModalScreen):
    """模型选择：数据 (provider, model, has_key) 列表 + 搜索过滤 + 滚动选择。

    dismiss((provider, model)) 确认；dismiss(None) 取消。
    """

    DEFAULT_CSS = """
    ModelPicker {
        align: center middle;
        background: rgba(0, 0, 0, 0.65);
    }
    #model-dialog {
        width: 72;
        height: auto;
        max-height: 85%;
        background: #1e2126;
        border: round #3d4451;
        padding: 1 2;
    }
    .model-title {
        color: #7bba55;
        text-style: bold;
        text-align: center;
        height: 1;
    }
    #model-search {
        margin: 1 0 1 0;
        border: round #3d4451;
        background: #171a1f;
    }
    #model-search:focus {
        border: round #7bba55;
    }
    #model-list {
        height: auto;
        max-height: 14;
    }
    #model-footer {
        color: #9aa0a6;
        height: 1;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        models: list[tuple[str, str, bool]],
        title: str = "选择模型",
    ) -> None:
        super().__init__()
        self._all: list[tuple[str, str, bool]] = list(models)
        self._filtered: list[tuple[str, str, bool]] = list(models)
        self._selected: int = 0
        self._title = title

    # ── compose / mount ────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static(self._title, classes="model-title")
            yield Input(placeholder="输入过滤模型名或 provider…", id="model-search")
            yield Static("", id="model-list")
            yield Static("↑/↓ 选择 · Enter 确认 · Esc 取消", id="model-footer")

    def on_mount(self) -> None:
        self.query_one("#model-search", Input).focus()
        self._refresh_list()

    # ── 过滤 ────────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "model-search":
            return
        query = event.value.strip().lower()
        if not query:
            self._filtered = list(self._all)
        else:
            self._filtered = [
                item for item in self._all
                if query in item[0].lower() or query in item[1].lower()
            ]
        self._selected = 0
        self._refresh_list()

    # ── 渲染 ────────────────────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        rows = self._visible_window()
        list_widget = self.query_one("#model-list", Static)
        lines = []
        for index, (provider, model, has_key) in rows:
            accent = MODEL_ACCENTS.get(provider, "#9aa0a6")
            selected = index == self._selected
            marker = "●" if selected else " "
            lock = "" if has_key else "  [需配置]"
            line = f"{marker} [{accent}]{provider}[/] {model}{lock}"
            if selected:
                line = f"[#ffffff bold]{line}[/]"
            elif not has_key:
                line = f"[#5c6370]{line}[/]"
            lines.append(line)
        if not lines:
            list_widget.update("[#9aa0a6]无匹配模型。[/]")
        else:
            list_widget.update("\n".join(lines))

    def _visible_window(self) -> list[tuple[int, tuple[str, str, bool]]]:
        total = len(self._filtered)
        if total == 0:
            return []
        start = max(0, min(self._selected - VISIBLE_ROWS + 1, total - VISIBLE_ROWS))
        return [
            (start + i, self._filtered[start + i])
            for i in range(min(VISIBLE_ROWS, total - start))
        ]

    # ── 键盘 ────────────────────────────────────────────────────────────────

    def on_key(self, event) -> None:
        if event.key == "up":
            self._selected = max(0, self._selected - 1)
            self._refresh_list()
            event.stop()
        elif event.key == "down":
            self._selected = min(len(self._filtered) - 1, self._selected + 1)
            self._refresh_list()
            event.stop()
        elif event.key == "enter":
            if self._filtered:
                provider, model, _ = self._filtered[self._selected]
                self.dismiss((provider, model))
            event.stop()
        elif event.key == "escape":
            self.dismiss(None)
            event.stop()

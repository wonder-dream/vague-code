"""首次使用引导（Setup Wizard，ADR-0037）。

首次 `vague-code tui` 且未配置 API key 时弹出：选择 provider → 填写参数 →
测试连接 → 写入全局配置（~/.config/vague-code/.env + config.json）→ 直接使用。
无跳过入口（未配置无法使用 TUI）。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RadioButton, RadioSet, Static

from vague_code.config import BUILTIN_PROVIDERS, DEFAULT_MODELS, build_backend


class SetupWizard(ModalScreen):
    """Provider 选择 + 参数填写 + 测试连接 + 完成写入（单屏多步状态机）。"""

    PROVIDER_LABELS = (
        ("deepseek", "DeepSeek（v4-flash / v4-pro）"),
        ("openai", "OpenAI GPT（gpt-5.6 系列）"),
        ("anthropic", "Anthropic（Fable 5 / Opus 5）"),
        ("custom", "自定义中转站（任意 OpenAI/Responses 兼容端点）"),
    )

    def __init__(self, app) -> None:
        super().__init__()
        self._app = app
        self._provider = "deepseek"
        self._testing = False

    # ── compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield Static("首次使用配置", classes="setup-title")
            yield Static(
                "选择模型服务商，然后填写 API Key 即可开始使用。",
                id="setup-desc",
            )
            with RadioSet(id="setup-provider"):
                for value, label in self.PROVIDER_LABELS:
                    yield RadioButton(label, id=f"provider-{value}")
            with Vertical(id="setup-fields"):
                yield Input(
                    placeholder="API Key（如 sk-...）",
                    password=True,
                    id="setup-key",
                )
                yield Input(
                    placeholder="中转站 Base URL（如 https://code.newcli.com/codex/v1）",
                    id="setup-baseurl",
                )
                yield Input(
                    placeholder="Key 环境变量名（默认 RELAY_KEY）",
                    id="setup-keyenv",
                    value="RELAY_KEY",
                )
                yield Input(
                    placeholder="模型名（如 gpt-5.6-sol，必填）",
                    id="setup-model",
                )
                yield Static("协议：", id="setup-protocol-label")
                with RadioSet(id="setup-protocol"):
                    yield RadioButton("OpenAI Chat Completions（默认）", id="protocol-openai")
                    yield RadioButton("Responses API", id="protocol-responses")
            yield Static("", id="setup-status")
            with Horizontal(id="setup-buttons"):
                yield Button("测试连接", id="setup-test", variant="primary")
                yield Button("完成并开始使用", id="setup-done", variant="success", disabled=True)

    # ── mount ────────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.query_one("#setup-provider", RadioSet).focus()
        self._sync_fields()

    # ── provider 选择 ────────────────────────────────────────────────────────

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        radio_set_id = event.radio_set.id
        if radio_set_id == "setup-provider":
            self._provider = str(event.pressed.id or "deepseek").removeprefix("provider-")
            self._sync_fields()

    def _sync_fields(self) -> None:
        is_custom = self._provider == "custom"
        for wid in ("setup-baseurl", "setup-keyenv", "setup-model", "setup-protocol-label", "setup-protocol"):
            self.query_one(f"#{wid}").display = is_custom
        key = self.query_one("#setup-key", Input)
        if is_custom:
            key.placeholder = "中转站 API Key"
        else:
            key.placeholder = "API Key（如 sk-...）"
        key.focus()
        self._update_done_enabled()

    # ── 输入变化 ─────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_done_enabled()

    def _update_done_enabled(self) -> None:
        key = self.query_one("#setup-key", Input).value.strip()
        done = self.query_one("#setup-done", Button)
        done.disabled = not key

    # ── 收集数据 ─────────────────────────────────────────────────────────────

    def _collect(self) -> dict:
        if self._provider == "custom":
            base_url = self.query_one("#setup-baseurl", Input).value.strip()
            key_env = self.query_one("#setup-keyenv", Input).value.strip() or "RELAY_KEY"
            model = self.query_one("#setup-model", Input).value.strip()
            protocol_set = self.query_one("#setup-protocol", RadioSet)
            pressed = protocol_set.pressed_button
            protocol = "responses" if (pressed is not None and pressed.id == "protocol-responses") else "openai"
            key = self.query_one("#setup-key", Input).value.strip()
            return {
                "provider": self._provider,
                "base_url": base_url,
                "key_env": key_env,
                "protocol": protocol,
                "model": model,
                "key": key,
            }
        builtin = BUILTIN_PROVIDERS[self._provider]
        return {
            "provider": self._provider,
            "base_url": str(builtin.get("baseUrl") or ""),
            "key_env": str(builtin.get("apiKeyEnv") or ""),
            "protocol": str(builtin.get("protocol") or "openai"),
            "model": DEFAULT_MODELS.get(self._provider, ""),
            "key": self.query_one("#setup-key", Input).value.strip(),
        }

    # ── 测试连接 ─────────────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup-test":
            self._start_test()
        elif event.button.id == "setup-done":
            self._finish()

    def _start_test(self) -> None:
        if self._testing:
            return
        data = self._collect()
        if not data["key"]:
            self._set_status("请先填写 API Key。", error=True)
            return
        if data["provider"] == "custom" and not data["base_url"]:
            self._set_status("请填写中转站 Base URL。", error=True)
            return
        if data["provider"] == "custom" and not data["model"]:
            self._set_status("请填写模型名。", error=True)
            return
        self._testing = True
        self.query_one("#setup-test", Button).disabled = True
        self._set_status("测试连接中…")
        self.run_worker(
            lambda: self._test_worker(data),
            thread=True, exclusive=False, group="setup-test",
        )

    def _test_worker(self, data: dict) -> None:
        from vague_code.agent.ir import Message
        try:
            backend = build_backend(
                data["provider"], data["key"], data["base_url"], data["protocol"], 30.0,
            )
            resp = backend.complete(
                [Message(role="user", content="ping")],
                tools=None,
                config={"model": data["model"]},
            )
            ok = bool(resp.message.content)
            self.app.call_from_thread(
                self._set_test_result, ok, "连接成功！" if ok else "连接失败：模型未返回内容",
            )
        except Exception as e:
            self.app.call_from_thread(
                self._set_test_result, False, f"连接失败：{type(e).__name__}: {e}",
            )

    def _set_test_result(self, ok: bool, message: str) -> None:
        self._testing = False
        self.query_one("#setup-test", Button).disabled = False
        self._set_status(message, error=not ok)

    def _set_status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one("#setup-status", Static)
        status.update(f"[{'#c85f5f' if error else '#7bba55'}]{message}[/]")

    # ── 完成 ─────────────────────────────────────────────────────────────────

    def _finish(self) -> None:
        data = self._collect()
        if not data["key"]:
            self._set_status("请先填写 API Key。", error=True)
            return
        if data["provider"] == "custom" and not data["model"]:
            self._set_status("请填写模型名。", error=True)
            return
        self._app._apply_setup(**data)
        self.dismiss(True)

    # ── 键盘（无跳过入口）───────────────────────────────────────────────────

    def on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()  # 引导必须完成，Esc 不关闭

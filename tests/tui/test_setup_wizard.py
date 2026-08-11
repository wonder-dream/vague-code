"""首次引导 SetupWizard 测试（ADR-0037）。"""

from __future__ import annotations

import json

from vague_code.agent.config import AgentConfig
from vague_code.tui.app import VagueCodeApp


class _FakeBackend:
    name = "fake"


def _make_app(tmp_path, **kwargs):
    config = AgentConfig(model="m", max_turns=5, db_path=str(tmp_path / "runs.db"))
    config.permission_mode = "normal"
    return VagueCodeApp(config=config, backend=_FakeBackend(), workdir=str(tmp_path), **kwargs)


async def test_setup_wizard_opens_when_needs_setup(tmp_path, monkeypatch) -> None:
    """needs_setup=True → on_mount 后弹出 SetupWizard。"""
    from vague_code.tui.screens.setup import SetupWizard

    app = _make_app(tmp_path, needs_setup=True)
    async with app.run_test() as pilot:
        for _ in range(50):
            if isinstance(app.screen, SetupWizard):
                break
            await pilot.pause(0.1)
        assert isinstance(app.screen, SetupWizard)


async def test_setup_wizard_not_opened_when_configured(tmp_path) -> None:
    app = _make_app(tmp_path, needs_setup=False)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        from vague_code.tui.screens.setup import SetupWizard
        assert not isinstance(app.screen, SetupWizard)


async def test_setup_wizard_select_provider_and_collect(tmp_path) -> None:
    """选择 provider 后输入区同步；内置只收集 key，自定义收集全套。"""
    from vague_code.tui.screens.setup import SetupWizard

    app = _make_app(tmp_path, needs_setup=True)
    async with app.run_test() as pilot:
        # needs_setup 经 call_after_refresh 异步推屏——轮询等待而非固定 pause（flaky 修复）
        for _ in range(100):
            if isinstance(app.screen, SetupWizard):
                break
            await pilot.pause(0.1)
        assert isinstance(app.screen, SetupWizard)
        await pilot.pause(0.05)  # 推屏后 compose 收尾缓冲（全量负载下偶发未就绪）
        wizard = app.screen
        # 默认 deepseek：baseUrl/keyEnv/model 输入隐藏
        assert wizard.query_one("#setup-baseurl").display is False
        # 切到自定义中转（直接驱动状态机，事件触发由 Textual 覆盖）
        wizard._provider = "custom"
        wizard._sync_fields()
        assert wizard.query_one("#setup-baseurl").display is True
        assert wizard.query_one("#setup-model").display is True
        # 填 key 后完成按钮启用
        wizard.query_one("#setup-key").value = "sk-test"
        wizard._update_done_enabled()
        assert wizard.query_one("#setup-done").disabled is False
        data = wizard._collect()
        assert data["provider"] == "custom"
        assert data["key"] == "sk-test"


async def test_setup_apply_writes_global_config(tmp_path, monkeypatch) -> None:
    """_apply_setup：写全局 .env + config.json + 重建 backend。"""
    import vague_code.config as cfg_mod

    fake_dir = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "global_config_dir", lambda: fake_dir)

    created: list = []

    class _RealBackend:
        name = "built"

    monkeypatch.setattr(
        cfg_mod, "build_backend",
        lambda provider, api_key, base_url, protocol, timeout_s: (created.append(1), _RealBackend())[1],
    )

    app = _make_app(tmp_path)
    app._apply_setup(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        protocol="openai",
        model="",
        key="sk-abc",
    )
    env_text = (fake_dir / ".env").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-abc" in env_text
    cfg = json.loads((fake_dir / "vague-code.json").read_text(encoding="utf-8"))
    assert cfg["defaultProvider"] == "deepseek"
    assert cfg["defaultModel"] == "deepseek-v4-flash"
    assert app._backend.name == "built"
    assert app._needs_setup is False
    assert app._config.model == "deepseek-v4-flash"


async def test_setup_custom_relay_writes_protocol_and_env_name(tmp_path, monkeypatch) -> None:
    """自定义中转：protocol 与自定义 key env 名写入配置。"""
    import vague_code.config as cfg_mod

    fake_dir = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "global_config_dir", lambda: fake_dir)
    monkeypatch.setattr(
        cfg_mod, "build_backend",
        lambda provider, api_key, base_url, protocol, timeout_s: type("B", (), {"name": "b"})(),
    )

    app = _make_app(tmp_path)
    app._apply_setup(
        provider="custom",
        base_url="https://code.newcli.com/codex/v1",
        key_env="RELAY_KEY",
        protocol="responses",
        model="gpt-5.6-sol",
        key="sk-relay",
    )
    env_text = (fake_dir / ".env").read_text(encoding="utf-8")
    assert "RELAY_KEY=sk-relay" in env_text
    cfg = json.loads((fake_dir / "vague-code.json").read_text(encoding="utf-8"))
    spec = cfg["providers"]["custom"]
    assert spec["baseUrl"] == "https://code.newcli.com/codex/v1"
    assert spec["protocol"] == "responses"
    assert spec["apiKeyEnv"] == "RELAY_KEY"
    assert cfg["defaultProvider"] == "custom"
    assert cfg["defaultModel"] == "gpt-5.6-sol"

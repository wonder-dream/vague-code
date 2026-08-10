"""vague-code.json 配置文件加载与合并（ADR-0033）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vague_code.config import (
    BUILTIN_PROVIDERS,
    load_config,
    provider_models,
    resolve_provider,
    write_init_template,
)


def test_defaults_when_no_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("vague_code.config._global_config_path", lambda: tmp_path / "none.json")
    cfg = load_config(tmp_path)
    assert cfg["defaultProvider"] == "deepseek"
    assert cfg["defaultModel"] == ""
    assert "fox" not in cfg["providers"]
    assert cfg["providers"]["deepseek"]["baseUrl"] == "https://api.deepseek.com"


def test_project_config_overrides_global(tmp_path, monkeypatch) -> None:
    global_cfg = {
        "defaultProvider": "global-p",
        "defaultModel": "global-m",
        "providers": {"myrelay": {"baseUrl": "https://global.example.com", "apiKeyEnv": "GLOBAL_KEY"}},
    }
    project_cfg = {
        "defaultProvider": "project-p",
        "providers": {"myrelay": {"baseUrl": "https://project.example.com", "apiKeyEnv": "PROJECT_KEY"}},
    }
    monkeypatch.setattr(
        "vague_code.config._global_config_path", lambda: _write(tmp_path, "global.json", global_cfg)
    )
    _write(tmp_path, "vague-code.json", project_cfg)
    cfg = load_config(tmp_path)
    # 项目优先
    assert cfg["defaultProvider"] == "project-p"
    assert cfg["defaultModel"] == "global-m"  # 项目未设置 → 继承全局
    # providers 深合并：项目覆盖同名、全局保留
    assert cfg["providers"]["myrelay"]["baseUrl"] == "https://project.example.com"
    assert cfg["providers"]["myrelay"]["apiKeyEnv"] == "PROJECT_KEY"
    # 内置 provider 始终存在，可被覆盖
    assert cfg["providers"]["deepseek"]["baseUrl"] == "https://api.deepseek.com"


def test_invalid_json_falls_back(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("vague_code.config._global_config_path", lambda: tmp_path / "none.json")
    (tmp_path / "vague-code.json").write_text("{not valid json", encoding="utf-8")
    with pytest.warns(UserWarning):
        cfg = load_config(tmp_path)
    assert cfg["defaultProvider"] == "deepseek"  # 回退内置默认，不崩溃


def test_resolve_provider_and_models(tmp_path) -> None:
    cfg = {
        "providers": {
            "fox": {
                "baseUrl": "https://code.newcli.com/codex/v1",
                "apiKeyEnv": "RELAY_KEY",
                "models": ["gpt-5.6-sol", "gpt-5.6-terra"],
            }
        }
    }
    spec = resolve_provider(cfg, "fox")
    assert spec["apiKeyEnv"] == "RELAY_KEY"
    assert provider_models(cfg, "fox") == ["gpt-5.6-sol", "gpt-5.6-terra"]
    # 未配置 models 的自定义 provider → 空列表
    assert provider_models({"providers": {"x": {"baseUrl": "u"}}}, "x") == []
    # 内置 provider 有默认模型列表
    assert provider_models(cfg, "deepseek") == ["deepseek-v4-flash", "deepseek-v4-pro",
                                                "deepseek-chat", "deepseek-reasoner"]


def test_init_template_writes_file(tmp_path) -> None:
    out = write_init_template(tmp_path / "vague-code.json")
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data["providers"]) >= set(BUILTIN_PROVIDERS)
    assert "my-relay" in data["providers"]


def _write(dir: Path, name: str, data: dict) -> Path:
    p = dir / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p

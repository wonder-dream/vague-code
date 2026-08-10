"""vague-code.json 配置加载与合并（ADR-0033）。

两级配置：全局 `~/.config/vague-code/config.json` + 项目运行目录 `vague-code.json`，
项目优先，providers 按名深合并。非法 JSON / 缺失文件 → 警告并回退，不崩溃。
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

CONFIG_FILENAME = "vague-code.json"

# 内置 provider 默认（可被配置文件覆盖）
BUILTIN_PROVIDERS: dict[str, dict] = {
    "deepseek": {"baseUrl": "https://api.deepseek.com", "apiKeyEnv": "DEEPSEEK_API_KEY"},
    "openai": {"baseUrl": "https://api.openai.com/v1", "apiKeyEnv": "OPENAI_API_KEY"},
    "anthropic": {
        "baseUrl": "https://api.deepseek.com/anthropic",
        "apiKeyEnv": "ANTHROPIC_API_KEY",
        "protocol": "anthropic",
    },
}

_BUILTIN_MODELS: dict[str, list[str]] = {
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
    "openai": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    "anthropic": ["claude-sonnet-4-5", "claude-opus-4-8"],
}


def _global_config_path() -> Path:
    return Path.home() / ".config" / "vague-code" / CONFIG_FILENAME


def _load_file(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        warnings.warn(f"Ignoring {path}: top level must be a JSON object", stacklevel=2)
    except (OSError, json.JSONDecodeError) as e:
        warnings.warn(f"Ignoring {path}: {e}", stacklevel=2)
    return None


def load_config(cwd: str | Path | None = None) -> dict:
    """合并两级配置：全局 + 项目（项目优先，providers 深合并）。"""
    global_cfg = _load_file(_global_config_path()) or {}
    project_cfg = _load_file(Path(cwd or ".") / CONFIG_FILENAME) or {}

    providers: dict[str, dict] = {}
    for name, spec in {**BUILTIN_PROVIDERS, **global_cfg.get("providers", {}),
                       **project_cfg.get("providers", {})}.items():
        if isinstance(spec, dict):
            providers[name] = dict(spec)

    merged = {
        "defaultProvider": project_cfg.get("defaultProvider")
                          or global_cfg.get("defaultProvider")
                          or "deepseek",
        "defaultModel": project_cfg.get("defaultModel") or global_cfg.get("defaultModel") or "",
        "providers": providers,
    }
    return merged


def resolve_provider(config: dict, provider: str) -> dict | None:
    """查 provider 配置（含内置），无则返回 None。"""
    return config.get("providers", {}).get(provider)


def provider_models(config: dict, provider: str) -> list[str]:
    """provider 的模型列表：配置指定 > 内置列表 > 空。"""
    spec = resolve_provider(config, provider)
    if spec and spec.get("models"):
        return [str(m) for m in spec["models"]]
    return _BUILTIN_MODELS.get(provider, [])


def write_init_template(path: str | Path) -> Path:
    """生成 vague-code.json 配置模板（含所有内置 provider 与自定义示例）。"""
    template = {
        "defaultProvider": "deepseek",
        "defaultModel": "",
        "providers": {
            "deepseek": {"baseUrl": "https://api.deepseek.com", "apiKeyEnv": "DEEPSEEK_API_KEY"},
            "openai": {"baseUrl": "https://api.openai.com/v1", "apiKeyEnv": "OPENAI_API_KEY"},
            "anthropic": {
                "baseUrl": "https://api.deepseek.com/anthropic",
                "apiKeyEnv": "ANTHROPIC_API_KEY",
                "protocol": "anthropic",
            },
            "my-relay": {
                "baseUrl": "https://你的中转站地址/v1",
                "apiKeyEnv": "RELAY_KEY",
                "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            },
        },
    }
    out = Path(path)
    out.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out

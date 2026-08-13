"""vague-code.json 配置加载与合并（ADR-0033）。

两级配置：全局 `~/.config/vague-code/config.json` + 项目运行目录 `vague-code.json`，
项目优先，providers 按名深合并。非法 JSON / 缺失文件 → 警告并回退，不崩溃。
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

CONFIG_FILENAME = "vague-code.json"

# 内置 provider 默认（可被配置文件覆盖）
BUILTIN_PROVIDERS: dict[str, dict] = {
    "deepseek": {"baseUrl": "https://api.deepseek.com", "apiKeyEnv": "DEEPSEEK_API_KEY"},
    "openai": {"baseUrl": "https://api.openai.com/v1", "apiKeyEnv": "OPENAI_API_KEY"},
    "anthropic": {
        "baseUrl": "https://api.anthropic.com",
        "apiKeyEnv": "ANTHROPIC_API_KEY",
        "protocol": "anthropic",
    },
}

# 现行模型目录（2026-08 官方核实；不再提供的老模型不列）
_BUILTIN_MODELS: dict[str, list[str]] = {
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro"],
    "openai": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    "anthropic": ["claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
}


def _global_config_path() -> Path:
    return Path.home() / ".config" / "vague-code" / CONFIG_FILENAME


def _global_env_path() -> Path:
    """全局 .env（ADR-0037）：首次引导把 API key 写这里。"""
    return Path.home() / ".config" / "vague-code" / ".env"


def global_config_dir() -> Path:
    return Path.home() / ".config" / "vague-code"


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


def all_provider_models(config: dict) -> list[tuple[str, str]]:
    """全部 (provider, model) 列表（内置目录 + 配置文件 providers.models）。

    provider 按名排序，模型按各自 provider 列表顺序；跨 provider 重名模型
    只保留先出现的（会话级 /model picker 用，ADR-0039）。
    """
    providers = config.get("providers", {})
    names = set(providers) | set(_BUILTIN_MODELS)
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for provider in sorted(names):
        for model in provider_models(config, provider):
            if model in seen:
                continue
            seen.add(model)
            items.append((provider, model))
    return items


def resolve_model_provider(config: dict, model: str) -> str | None:
    """反查模型所属 provider：配置 providers.models 优先，其次内置目录。"""
    for provider, spec in config.get("providers", {}).items():
        if not isinstance(spec, dict):
            continue
        if model in {str(m) for m in spec.get("models", [])}:
            return provider
    for provider, models in _BUILTIN_MODELS.items():
        if model in models:
            return provider
    return None


def write_init_template(path: str | Path) -> Path:
    """生成 vague-code.json 配置模板（含所有内置 provider 与自定义示例）。"""
    template = {
        "defaultProvider": "deepseek",
        "defaultModel": "",
        "providers": {
            "deepseek": {"baseUrl": "https://api.deepseek.com", "apiKeyEnv": "DEEPSEEK_API_KEY"},
            "openai": {"baseUrl": "https://api.openai.com/v1", "apiKeyEnv": "OPENAI_API_KEY"},
            "anthropic": {
                "baseUrl": "https://api.anthropic.com",
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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


# ── 首次引导写入（ADR-0037）──────────────────────────────────────────────

DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-5.6-sol",
    "anthropic": "claude-fable-5",
}


def write_env_key(env_path: str | Path, key_env: str, key: str) -> Path:
    """把 API key 写入 .env（合并更新，不破坏已有其他键）。"""
    out = Path(env_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if out.is_file():
        lines = out.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key_env + "="):
            lines[i] = f"{key_env}={key}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key_env}={key}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
    return out


def merge_provider_config(
    path: str | Path,
    provider: str,
    spec: dict,
    *,
    default_model: str = "",
) -> Path:
    """把 provider 配置合并进 vague-code.json（保留已有条目与内置默认）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if out.is_file():
        try:
            loaded = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    providers = dict(data.get("providers", {}))
    merged = {**BUILTIN_PROVIDERS.get(provider, {}), **spec}
    providers[provider] = merged
    data["providers"] = providers
    if provider == "deepseek" or "defaultProvider" not in data:
        data["defaultProvider"] = provider
    if default_model:
        data["defaultModel"] = default_model
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def build_backend(
    provider: str,
    api_key: str,
    base_url: str,
    protocol: str,
    timeout_s: float,
    user_agent: str | None = None,
):
    """按协议构造后端（从 cli 移入，供引导/CLI 共用，ADR-0037）。

    user_agent：自定义 User-Agent（中转站放行策略需要时设置，如 claude-cli/）。
    """
    from vague_code.agent.backend import (
        create_anthropic_backend,
        create_deepseek_backend,
        create_responses_backend,
    )

    if protocol == "anthropic":
        return create_anthropic_backend(
            api_key=api_key, base_url=base_url, timeout_s=timeout_s, user_agent=user_agent
        )
    if protocol == "responses":
        return create_responses_backend(api_key=api_key, base_url=base_url, timeout_s=timeout_s)
    return create_deepseek_backend(api_key=api_key, base_url=base_url, timeout_s=timeout_s)

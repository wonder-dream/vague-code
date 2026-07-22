from __future__ import annotations

from typing import Protocol

from openai import OpenAI

from src.agent.codecs.deepseek import encode_request, decode_response
from src.agent.ir import Message, ModelResponse, ToolSpec


class ModelBackend(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse: ...


class DeepSeekBackend:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        timeout_s: float = 120.0,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse:
        body = encode_request(messages, tools, config)
        model = "deepseek-v4-flash"
        if isinstance(config, dict):
            model = config.get("model", model)
        body["model"] = model
        raw = self._client.chat.completions.create(**body)
        return decode_response(raw.model_dump(mode="json"))


def create_deepseek_backend(
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    timeout_s: float = 120.0,
) -> DeepSeekBackend:
    return DeepSeekBackend(api_key=api_key, base_url=base_url, timeout_s=timeout_s)

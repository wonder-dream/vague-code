from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from openai import OpenAI

from src.agent.codecs.deepseek import (
    DeepSeekStreamDecoder,
    decode_response,
    encode_request,
)
from src.agent.ir import (
    Message,
    MessageStart,
    ModelResponse,
    StreamEvent,
    ToolSpec,
)


class ModelBackend(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse: ...

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> Iterator[StreamEvent]: ...


class DeepSeekBackend:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        timeout_s: float = 120.0,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=2)

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
        body.pop("stream", None)  # 非流式请求不应带 stream 键
        raw = self._client.chat.completions.create(**body)
        return decode_response(raw.model_dump(mode="json"))

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> Iterator[StreamEvent]:
        body = encode_request(messages, tools, config)
        model = "deepseek-v4-flash"
        if isinstance(config, dict):
            model = config.get("model", model)
        body["model"] = model
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}

        raw_iter = self._client.chat.completions.create(**body)
        first = True
        decoder = DeepSeekStreamDecoder()
        for chunk in raw_iter:
            d = chunk.model_dump(mode="json")
            if first:
                yield MessageStart(model=d.get("model") or model)
                first = False
            yield from decoder.decode_chunk(d)
        yield from decoder.flush()


def create_deepseek_backend(
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    timeout_s: float = 120.0,
) -> DeepSeekBackend:
    return DeepSeekBackend(api_key=api_key, base_url=base_url, timeout_s=timeout_s)

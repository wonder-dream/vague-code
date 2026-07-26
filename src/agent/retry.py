from __future__ import annotations

import json
import random
from dataclasses import dataclass

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

_ANTHROPIC_RETRYABLE: tuple[type, ...] = ()
try:
    from anthropic import (  # type: ignore[import-untyped]
        APIConnectionError as _AnthropicConn,
        APITimeoutError as _AnthropicTimeout,
        InternalServerError as _AnthropicServer,
        RateLimitError as _AnthropicRate,
    )
    _ANTHROPIC_RETRYABLE = (_AnthropicConn, _AnthropicTimeout, _AnthropicServer, _AnthropicRate)
except ImportError:
    pass

from src.agent.config import TransportConfig  # noqa: E402
from src.agent.ir import (  # noqa: E402
    Message,
    ModelResponse,
    StreamDisconnect,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    reason: str
    error_kind: str
    terminal_reason: str


@dataclass(frozen=True)
class RetryPolicy:
    enabled: bool = True
    max_attempts: int = 5
    base_s: float = 2.0
    max_delay_s: float = 120.0

    @classmethod
    def from_config(cls, tc: TransportConfig) -> RetryPolicy:
        return cls(
            enabled=tc.retry_enabled,
            max_attempts=tc.retry_max_attempts,
            base_s=tc.retry_base_s,
            max_delay_s=tc.retry_max_delay_s,
        )

    def delay(self, retry_index: int) -> float:
        cap = min(self.max_delay_s, self.base_s * (2 ** retry_index))
        return random.uniform(0, cap)


def classify_llm_error(exc: BaseException) -> RetryDecision:
    if isinstance(exc, APITimeoutError):
        return RetryDecision(
            retryable=True, reason="timeout",
            error_kind="llm_timeout", terminal_reason="llm_timeout",
        )
    if isinstance(exc, APIConnectionError):
        return RetryDecision(
            retryable=True, reason="connection",
            error_kind="connection_error", terminal_reason="llm_error",
        )
    if isinstance(exc, RateLimitError):
        return RetryDecision(
            retryable=True, reason="rate_limit",
            error_kind="rate_limit", terminal_reason="llm_error",
        )
    if isinstance(exc, InternalServerError):
        return RetryDecision(
            retryable=True, reason="server_error",
            error_kind="server_error", terminal_reason="llm_error",
        )
    if isinstance(exc, StreamDisconnect):
        return RetryDecision(
            retryable=True, reason="stream_disconnect",
            error_kind="stream_disconnect", terminal_reason="llm_error",
        )
    if _ANTHROPIC_RETRYABLE and isinstance(exc, _ANTHROPIC_RETRYABLE):
        if isinstance(exc, _AnthropicRate):
            return RetryDecision(
                retryable=True, reason="rate_limit",
                error_kind="rate_limit", terminal_reason="llm_error",
            )
        return RetryDecision(
            retryable=True, reason="server_error",
            error_kind="llm_error", terminal_reason="llm_error",
        )
    if isinstance(exc, (BadRequestError, AuthenticationError, PermissionDeniedError, NotFoundError, UnprocessableEntityError)):
        return RetryDecision(
            retryable=False, reason="client_error",
            error_kind="llm_error", terminal_reason="llm_error",
        )
    if isinstance(exc, (ValueError, TypeError)):
        return RetryDecision(
            retryable=False, reason="codec_error",
            error_kind="codec_error", terminal_reason="llm_error",
        )
    return RetryDecision(
        retryable=False, reason="unknown",
        error_kind="llm_error", terminal_reason="llm_error",
    )


def estimate_input_tokens(messages: list[Message], tools: list[ToolSpec] | None = None) -> int:
    total = 0
    for msg in messages:
        total += 4
        for block in msg.content:
            if isinstance(block, (TextBlock, ThinkingBlock)):
                total += len(block.text) // 4 + 4
            elif isinstance(block, ToolUseBlock):
                total += len(block.name) // 4 + len(json.dumps(block.input, ensure_ascii=False)) // 4 + 8
            elif isinstance(block, ToolResultBlock):
                total += len(block.content) // 4 + 8
    for tool in tools or []:
        total += len(json.dumps(tool.to_openai_tool(), ensure_ascii=False)) // 4 + 8
    return max(total, 1)


def response_signature(resp: ModelResponse) -> dict:
    return {
        "stop_reason": resp.stop_reason.value,
        "tools": [
            {
                "name": b.name,
                "args_canonical": json.dumps(b.input, sort_keys=True, ensure_ascii=False),
            }
            for b in resp.message.content
            if isinstance(b, ToolUseBlock)
        ],
    }

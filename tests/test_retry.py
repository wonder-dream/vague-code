from __future__ import annotations

import json
import random

import httpx
import pytest

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

from src.agent.config import TransportConfig
from src.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    ToolUseBlock,
)
from src.agent.retry import (
    RetryPolicy,
    classify_llm_error,
    estimate_input_tokens,
    response_signature,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_request() -> httpx.Request:
    url = "https://api.deepseek.com/v1/chat/completions"
    return httpx.Request("POST", url)


def _make_timeout_error() -> APITimeoutError:
    return APITimeoutError(request=_make_request())


def _make_connection_error() -> APIConnectionError:
    return APIConnectionError(message="connection failed", request=_make_request())


def _make_rate_limit(status: int = 429) -> RateLimitError:
    resp = httpx.Response(status_code=status, request=_make_request())
    return RateLimitError("rate limited", response=resp, body=None)


def _make_internal_server(status: int = 500) -> InternalServerError:
    resp = httpx.Response(status_code=status, request=_make_request())
    return InternalServerError("server error", response=resp, body=None)


# ── RetryPolicy.delay ────────────────────────────────────────────────────────


class TestRetryPolicyDelay:
    def test_delay_caps_sequence(self):
        policy = RetryPolicy(max_attempts=10)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(random, "uniform", lambda lo, hi: hi)
            delays = [policy.delay(i) for i in range(8)]
            assert delays == [2, 4, 8, 16, 32, 64, 120, 120]

    def test_delay_has_minimum_floor(self):
        policy = RetryPolicy(max_attempts=3)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(random, "uniform", lambda lo, hi: 0.0)
            assert policy.delay(0) == 0.5
            assert policy.delay(1) == 0.5

    def test_delay_negative_max_attempts_raises(self):
        with pytest.raises(ValueError, match="retry_max_attempts"):
            TransportConfig(retry_max_attempts=-1)

    def test_delay_zero_base_raises(self):
        with pytest.raises(ValueError, match="retry_base_s"):
            TransportConfig(retry_base_s=0)

    def test_delay_zero_max_delay_raises(self):
        with pytest.raises(ValueError, match="retry_max_delay_s"):
            TransportConfig(retry_max_delay_s=0)

    def test_from_config(self):
        tc = TransportConfig(retry_enabled=False, retry_max_attempts=2, retry_base_s=1.0)
        policy = RetryPolicy.from_config(tc)
        assert policy.enabled is False
        assert policy.max_attempts == 2
        assert policy.base_s == 1.0


# ── classify_llm_error ───────────────────────────────────────────────────────


class TestClassifyLlmError:
    def _assert(self, exc: BaseException, *, retryable: bool, reason: str, error_kind: str, terminal_reason: str):
        d = classify_llm_error(exc)
        assert d.retryable == retryable
        assert d.reason == reason
        assert d.error_kind == error_kind
        assert d.terminal_reason == terminal_reason

    def test_timeout(self):
        self._assert(_make_timeout_error(), retryable=True, reason="timeout", error_kind="llm_timeout", terminal_reason="llm_timeout")

    def test_connection_error(self):
        self._assert(_make_connection_error(), retryable=True, reason="connection", error_kind="connection_error", terminal_reason="llm_error")

    def test_rate_limit(self):
        self._assert(_make_rate_limit(), retryable=True, reason="rate_limit", error_kind="rate_limit", terminal_reason="llm_error")

    def test_server_error(self):
        self._assert(_make_internal_server(), retryable=True, reason="server_error", error_kind="server_error", terminal_reason="llm_error")

    def test_bad_request(self):
        resp = httpx.Response(400, request=_make_request())
        self._assert(BadRequestError("bad", response=resp, body=None), retryable=False, reason="client_error", error_kind="llm_error", terminal_reason="llm_error")

    def test_auth_error(self):
        resp = httpx.Response(401, request=_make_request())
        self._assert(AuthenticationError("auth", response=resp, body=None), retryable=False, reason="client_error", error_kind="llm_error", terminal_reason="llm_error")

    def test_permission_error(self):
        resp = httpx.Response(403, request=_make_request())
        self._assert(PermissionDeniedError("perm", response=resp, body=None), retryable=False, reason="client_error", error_kind="llm_error", terminal_reason="llm_error")

    def test_not_found(self):
        resp = httpx.Response(404, request=_make_request())
        self._assert(NotFoundError("not found", response=resp, body=None), retryable=False, reason="client_error", error_kind="llm_error", terminal_reason="llm_error")

    def test_unprocessable(self):
        resp = httpx.Response(422, request=_make_request())
        self._assert(UnprocessableEntityError("unproc", response=resp, body=None), retryable=False, reason="client_error", error_kind="llm_error", terminal_reason="llm_error")

    def test_value_error(self):
        self._assert(ValueError("bad"), retryable=False, reason="codec_error", error_kind="codec_error", terminal_reason="llm_error")

    def test_type_error(self):
        self._assert(TypeError("bad type"), retryable=False, reason="codec_error", error_kind="codec_error", terminal_reason="llm_error")

    def test_unknown_exception(self):
        self._assert(RuntimeError("uh oh"), retryable=False, reason="unknown", error_kind="llm_error", terminal_reason="llm_error")

    def test_stream_disconnect(self):
        from src.agent.ir import StreamDisconnect
        self._assert(StreamDisconnect("stream dropped"), retryable=True, reason="stream_disconnect", error_kind="stream_disconnect", terminal_reason="llm_error")


# ── estimate_input_tokens ────────────────────────────────────────────────────


class TestEstimateInputTokens:
    def test_empty_messages(self):
        assert estimate_input_tokens([], tools=None) >= 1

    def test_single_text_message(self):
        msgs = [Message(role="user", content="hello world")]
        assert estimate_input_tokens(msgs) > 4

    def test_with_tool_use_block(self):
        msgs = [
            Message(role="assistant", content=[
                TextBlock(text="Let me check"),
                ToolUseBlock(id="c1", name="read_file", input={"path": "foo.txt"}),
            ]),
        ]
        t = estimate_input_tokens(msgs)
        assert t >= 10

    def test_with_tools(self):
        from src.agent.ir import ToolSpec
        msgs = [Message(role="user", content="Read README")]
        tools = [ToolSpec(name="read_file", description="Read a file", parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})]
        t = estimate_input_tokens(msgs, tools)
        assert t >= 4


# ── response_signature ───────────────────────────────────────────────────────


class TestResponseSignature:
    def test_text_only(self):
        resp = ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text="done")]),
            stop_reason=StopReason.end_turn,
            usage=NormalizedUsage(input_tokens=1, output_tokens=1),
        )
        sig = response_signature(resp)
        assert sig["stop_reason"] == "end_turn"
        assert sig["tools"] == []

    def test_tool_use(self):
        resp = ModelResponse(
            message=Message(role="assistant", content=[
                ToolUseBlock(id="c1", name="read_file", input={"path": "a.txt"}),
                ToolUseBlock(id="c2", name="grep", input={"pattern": "TODO"}),
            ]),
            stop_reason=StopReason.tool_use,
            usage=NormalizedUsage(input_tokens=5, output_tokens=3),
        )
        sig = response_signature(resp)
        assert len(sig["tools"]) == 2
        assert sig["tools"][0]["name"] == "read_file"
        assert sig["tools"][0]["args_canonical"] == json.dumps({"path": "a.txt"}, sort_keys=True)

    def test_signature_same_for_different_ids(self):
        r1 = ModelResponse(
            message=Message(role="assistant", content=[ToolUseBlock(id="x1", name="read", input={"path": "f.txt"})]),
            stop_reason=StopReason.tool_use, usage=NormalizedUsage(1, 1),
        )
        r2 = ModelResponse(
            message=Message(role="assistant", content=[ToolUseBlock(id="x2", name="read", input={"path": "f.txt"})]),
            stop_reason=StopReason.tool_use, usage=NormalizedUsage(1, 1),
        )
        assert response_signature(r1) == response_signature(r2)

    def test_signature_different_for_different_tools(self):
        r1 = ModelResponse(
            message=Message(role="assistant", content=[ToolUseBlock(id="c1", name="read", input={"path": "a.txt"})]),
            stop_reason=StopReason.tool_use, usage=NormalizedUsage(1, 1),
        )
        r2 = ModelResponse(
            message=Message(role="assistant", content=[ToolUseBlock(id="c2", name="read", input={"path": "b.txt"})]),
            stop_reason=StopReason.tool_use, usage=NormalizedUsage(1, 1),
        )
        assert response_signature(r1) != response_signature(r2)

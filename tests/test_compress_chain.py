from __future__ import annotations

from vague_code.agent.context_compress import compress_chain
from vague_code.agent.config import CompressionConfig
from vague_code.agent.context_tokens import compute_budget, count_tokens
from vague_code.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class _FakeBackend:
    def __init__(self, summary: str = "Summary of past conversations."):
        self.summary = summary
        self.call_count = 0

    def complete(
        self,
        messages: list[Message],
        tools: list | None = None,
        config: dict | None = None,
    ) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text=self.summary)]),
            stop_reason=StopReason.end_turn,
            usage=NormalizedUsage(input_tokens=50, output_tokens=15),
        )


def _make_session(n_pairs: int) -> list[Message]:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="Fix the bug"),
    ]
    for i in range(n_pairs):
        msgs.append(Message(role="assistant", content=[
            ToolUseBlock(id=f"c{i}", name="read", input={"path": "a.py"}),
        ]))
        msgs.append(Message(role="user", content=[
            ToolResultBlock(tool_use_id=f"c{i}", content="A" * 5000),
        ]))
    return msgs


def test_low_utilization_stale_only():
    backend = _FakeBackend()
    cfg = CompressionConfig()
    msgs = _make_session(2)
    budget = compute_budget("deepseek-v4-flash")  # large budget → very low util
    result, reports = compress_chain(msgs, None, cfg, budget, backend=backend, model="test")
    layers = [r.layer for r in reports]
    assert "stale_snip" in layers
    # No auto_compact or truncate needed at low utilization
    assert backend.call_count == 0
    assert len(result) > 0


def test_high_utilization_triggers_microcompact():
    backend = _FakeBackend()
    cfg = CompressionConfig(
        microcompact_threshold=0.01,
        auto_compact_threshold=1.0,
    )
    msgs = _make_session(3)
    budget = count_tokens(msgs, skip_thinking=True) + 100
    result, reports = compress_chain(msgs, None, cfg, budget, backend=backend, model="test")
    layers = [r.layer for r in reports]
    assert "stale_snip" in layers
    assert "microcompact" in layers
    assert backend.call_count == 0


def test_auto_compact_triggered():
    backend = _FakeBackend("The task was to fix a bug. User read a.py several times.")
    cfg = CompressionConfig(
        microcompact_threshold=0.01,
        auto_compact_threshold=0.01,
    )
    msgs = _make_session(5)
    budget = count_tokens(msgs, skip_thinking=True) + 100
    result, reports = compress_chain(msgs, None, cfg, budget, backend=backend, model="test")
    layers = [r.layer for r in reports]
    assert "auto_compact" in layers
    assert backend.call_count >= 1


def test_truncation_triggered():
    msgs = _make_session(10)
    budget = count_tokens(msgs[:4], skip_thinking=True) + 20
    cfg = CompressionConfig(
        microcompact_threshold=0.01,
        auto_compact_threshold=0.01,
    )
    result, reports = compress_chain(msgs, None, cfg, budget, model="test")
    layers = [r.layer for r in reports]
    assert "truncate" in layers
    assert count_tokens(result, skip_thinking=True) <= budget


def test_chain_order_preserved():
    backend = _FakeBackend("summary text")
    cfg = CompressionConfig(
        microcompact_threshold=0.01,
        auto_compact_threshold=0.01,
    )
    msgs = _make_session(5)
    budget = count_tokens(msgs, skip_thinking=True) + 100
    result, reports = compress_chain(msgs, None, cfg, budget, backend=backend, model="test")
    expected_order = ["stale_snip", "microcompact", "auto_compact"]
    actual_layers = [r.layer for r in reports if r.layer in expected_order]
    assert actual_layers == expected_order


def test_enabled_true_produces_reports():
    backend = _FakeBackend()
    cfg = CompressionConfig(enabled=True)
    msgs = _make_session(3)
    budget = count_tokens(msgs, skip_thinking=True) + 100
    result, reports = compress_chain(msgs, None, cfg, budget, backend=backend, model="test")
    assert len(reports) >= 1
    # When enabled, stale_snip always runs
    assert "stale_snip" in [r.layer for r in reports]


def test_enabled_false_noop():
    backend = _FakeBackend()
    cfg = CompressionConfig(enabled=False)
    msgs = _make_session(5)
    budget = count_tokens(msgs, skip_thinking=True) + 100
    result, reports = compress_chain(msgs, None, cfg, budget, backend=backend, model="test")
    assert len(reports) == 0
    # Messages unchanged
    assert len(result) == len(msgs)

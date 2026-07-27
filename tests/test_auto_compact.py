from __future__ import annotations

from src.agent.context_compress import auto_compact
from src.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


class _FakeSummaryBackend:
    def __init__(self, summary: str = "Session summary placeholder"):
        self.summary = summary
        self.call_count = 0
        self.last_messages: list[Message] | None = None

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse:
        self.call_count += 1
        self.last_messages = messages
        return ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text=self.summary)]),
            stop_reason=StopReason.end_turn,
            usage=NormalizedUsage(input_tokens=50, output_tokens=10),
        )


def test_summary_request_contains_history() -> None:
    backend = _FakeSummaryBackend()
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="Read the file"),
        Message(role="assistant", content=[TextBlock(text="I read it")]),
        Message(role="user", content=[TextBlock(text="Now edit it")]),
        Message(role="assistant", content=[TextBlock(text="Done")]),
        Message(role="user", content=[TextBlock(text="Check results")]),
    ]
    result, report = auto_compact(msgs, backend, "test-model", keep_turns=1)
    assert backend.call_count == 1
    assert report.affected > 0
    # History text should be in the request
    assert backend.last_messages is not None
    req_text = "".join(b.text for b in backend.last_messages[0].content if isinstance(b, TextBlock))
    assert "Read the file" in req_text
    assert "I read it" in req_text
    assert "check results" not in req_text or "Check results" not in req_text


def test_rebuild_structure() -> None:
    backend = _FakeSummaryBackend("Summarized: explored codebase.")
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="Explore the repo"),
        Message(role="assistant", content=[TextBlock(text="Found files")]),
        Message(role="user", content=[TextBlock(text="Run tests")]),
        Message(role="assistant", content=[TextBlock(text="Tests pass")]),
        Message(role="user", content=[TextBlock(text="Now build")]),
        Message(role="assistant", content=[TextBlock(text="Check results")]),
        Message(role="user", content=[TextBlock(text="All green")]),
    ]
    result, report = auto_compact(msgs, backend, "test-model", keep_turns=2)
    assert report.affected > 0
    assert result[0].role == "system"
    assert "[Session summary]" in result[1].content[0].text
    assert "All green" in result[-1].content[0].text
    assert report.detail.get("original_messages", 0) > 0


def test_system_preserved() -> None:
    backend = _FakeSummaryBackend("summary")
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[TextBlock(text="done")]),
        Message(role="user", content=[TextBlock(text="ok")]),
        Message(role="assistant", content=[TextBlock(text="verify")]),
        Message(role="user", content=[TextBlock(text="pass")]),
    ]
    result, report = auto_compact(msgs, backend, "test-model", keep_turns=1)
    assert result[0].role == "system"
    assert result[0].content[0].text == "You are a coding agent."


def test_failure_graceful() -> None:
    class _FailingBackend:
        def complete(self, messages, tools=None, config=None):
            raise RuntimeError("API failure")

    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[TextBlock(text="done")]),
        Message(role="user", content=[TextBlock(text="ok")]),
        Message(role="assistant", content=[TextBlock(text="verify")]),
        Message(role="user", content=[TextBlock(text="pass")]),
    ]
    result, report = auto_compact(msgs, _FailingBackend(), "test-model", keep_turns=1)
    assert report.affected == 0
    assert "error" in report.detail
    assert len(result) == len(msgs)


def test_empty_summary_skips() -> None:
    class _EmptyBackend:
        def complete(self, messages, tools=None, config=None):
            return ModelResponse(
                message=Message(role="assistant", content=[ThinkingBlock(text="thinking only")]),
                stop_reason=StopReason.end_turn, usage=NormalizedUsage(5, 2))

    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[TextBlock(text="done")]),
        Message(role="user", content=[TextBlock(text="ok")]),
        Message(role="assistant", content=[TextBlock(text="verify")]),
        Message(role="user", content=[TextBlock(text="pass")]),
    ]
    result, report = auto_compact(msgs, _EmptyBackend(), "test-model", keep_turns=1)
    assert report.affected == 0
    assert report.detail.get("skipped") == "empty_summary_from_model"
    assert len(result) == len(msgs)


def test_summary_includes_tool_calls() -> None:
    backend = _FakeSummaryBackend("tool work done")
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="Read and run"),
        Message(role="assistant", content=[ToolUseBlock(id="c1", name="read", input={"path": "a.py"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c1", content="file content here")]),
        Message(role="assistant", content=[TextBlock(text="Reading done")]),
        Message(role="user", content=[TextBlock(text="Now check")]),
        Message(role="assistant", content=[TextBlock(text="All good")]),
        Message(role="user", content=[TextBlock(text="ok")]),
    ]
    result, report = auto_compact(msgs, backend, "test-model", keep_turns=1)
    assert report.affected > 0
    assert backend.last_messages is not None
    req_text = "".join(b.text for b in backend.last_messages[0].content if isinstance(b, TextBlock))
    assert "[tool:" in req_text
    assert "[result:" in req_text
    assert "read" in req_text
    assert "a.py" in req_text


def test_too_few_messages_skips() -> None:
    backend = _FakeSummaryBackend()
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
    ]
    result, report = auto_compact(msgs, backend, "test-model", keep_turns=4)
    assert report.affected == 0
    assert report.detail.get("skipped") is not None
    assert backend.call_count == 0

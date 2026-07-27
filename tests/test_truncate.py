from __future__ import annotations

from src.agent.context_compress import truncate
from src.agent.context_tokens import count_tokens
from src.agent.ir import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def test_under_budget_noop() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[TextBlock(text="done")]),
    ]
    budget = count_tokens(msgs, skip_thinking=True) * 2
    result, report = truncate(msgs, budget, skip_thinking=True)
    assert report.affected == 0
    assert len(result) == len(msgs)


def test_budget_respected() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
    ]
    for i in range(10):
        msgs.append(Message(role="assistant", content=[TextBlock(text=f"response {i}")]))
        msgs.append(Message(role="user", content=[TextBlock(text=f"user followup {i}")]))
    budget = count_tokens(msgs[:3], skip_thinking=True) + 30
    result, report = truncate(msgs, budget, skip_thinking=True)
    assert report.affected > 0
    assert count_tokens(result, skip_thinking=True) <= budget


def test_system_and_task_preserved() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="Do the thing"),
    ]
    for i in range(20):
        msgs.append(Message(role="assistant", content=[TextBlock(text=f"r{i}")]))
        msgs.append(Message(role="user", content=[TextBlock(text=f"u{i}")]))
    budget = count_tokens(msgs[:3], skip_thinking=True) + 30
    result, report = truncate(msgs, budget, skip_thinking=True)
    assert result[0].role == "system"
    assert result[0].content[0].text == "You are a coding agent."
    assert result[1].role == "user"
    assert result[1].content[0].text == "Do the thing"


def test_tool_pair_atomicity() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[ToolUseBlock(id="c1", name="read", input={"path": "x"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c1", content="file content")]),
        Message(role="assistant", content=[TextBlock(text="final")]),
        Message(role="user", content=[TextBlock(text="ok")]),
    ]
    # Budget can hold system + first user + one additional pair
    budget = count_tokens(msgs[:2], skip_thinking=True) + count_tokens(msgs[4:6], skip_thinking=True) + 5
    result, report = truncate(msgs, budget, skip_thinking=True)
    # assistant with ToolUseBlock must be dropped together with its following user
    assert report.affected > 0


def test_standalone_pair_not_broken() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[ToolUseBlock(id="c1", name="bash", input={"cmd": "x"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c1", content="output")]),
        Message(role="user", content=[TextBlock(text="standalone query")]),
    ]
    tight_budget = count_tokens(msgs[:2], skip_thinking=True) + 8
    result, report = truncate(msgs, tight_budget, skip_thinking=True)
    # If the tool pair fits it must be kept together; if it doesn't, both must be dropped
    tool_use_indices = [i for i, m in enumerate(result) if any(
        isinstance(b, ToolUseBlock) for b in m.content)]
    tool_result_indices = [i for i, m in enumerate(result) if any(
        isinstance(b, ToolResultBlock) for b in m.content)]
    # Every ToolUseBlock must have its ToolResultBlock, and vice versa
    assert len(tool_use_indices) == len(tool_result_indices)
    # The pair must be consecutive (assistant then user)
    for tu, tr in zip(tool_use_indices, tool_result_indices):
        assert tu == tr - 1


def test_truncation_marker() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
    ]
    for i in range(10):
        msgs.append(Message(role="assistant", content=[TextBlock(text=f"r{i}")]))
        msgs.append(Message(role="user", content=[TextBlock(text=f"u{i}")]))
    budget = count_tokens(msgs[:2], skip_thinking=True) + 20
    result, report = truncate(msgs, budget, skip_thinking=True)
    markers = [m for m in result if "truncated" in m.content[0].text]
    assert len(markers) >= 1


def test_marker_overflow_with_standalone() -> None:
    msgs = [
        Message(role="system", content="You are a coding agent."),
        Message(role="user", content="task"),
        Message(role="assistant", content=[TextBlock(text="assistant reply")]),
        Message(role="user", content=[TextBlock(text="user followup")]),
        Message(role="user", content=[TextBlock(text="standalone trailing")]),
    ]
    # Budget fits prefix + 1-2 messages but not all + marker
    tight_budget = count_tokens(msgs[:2], skip_thinking=True) + 10
    result, report = truncate(msgs, tight_budget, skip_thinking=True)
    # Should not crash, should respect budget
    assert count_tokens(result, skip_thinking=True) <= tight_budget


def test_single_message_no_truncation() -> None:
    msgs = [Message(role="user", content="hello")]
    budget = count_tokens(msgs, skip_thinking=True) - 1
    result, report = truncate(msgs, budget, skip_thinking=True)
    assert report.affected == 0
    assert len(result) == 1


def test_system_only_no_task() -> None:
    msgs = [Message(role="system", content="You are a coding agent.")]
    budget = 1  # tiny
    result, report = truncate(msgs, budget, skip_thinking=True)
    assert report.affected == 0
    assert len(result) == 1


def test_no_system_message() -> None:
    msgs = [
        Message(role="user", content="task"),
    ]
    for i in range(15):
        msgs.append(Message(role="assistant", content=[TextBlock(text=f"r{i}")]))
        msgs.append(Message(role="user", content=[TextBlock(text=f"u{i}")]))
    budget = count_tokens(msgs[:1], skip_thinking=True) + 30
    result, report = truncate(msgs, budget, skip_thinking=True)
    assert result[0].role == "user"
    assert count_tokens(result, skip_thinking=True) <= budget
    assert report.affected > 0

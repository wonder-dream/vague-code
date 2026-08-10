"""Chat session tests: multi-turn conversation, context continuity, resume."""

from __future__ import annotations

from vague_code.agent.config import AgentConfig
from vague_code.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from vague_code.agent.loop import Agent
from vague_code.agent.trajectory import EventType, Trajectory


class _SmartFakeBackend:
    """Fake backend: tools=None calls are summarization, normal calls consume responses."""

    def __init__(self, responses, summary_text: str = "[Prior turns summarized.]"):
        self.responses = responses
        self.call_count = 0
        self.summary_count = 0
        self.seen_messages: list[list[Message]] = []
        self._summary_text = summary_text

    def complete(self, messages, tools=None, config=None):
        if tools is None:
            self.summary_count += 1
            return ModelResponse(
                message=Message(role="assistant", content=[TextBlock(text=self._summary_text)]),
                stop_reason=StopReason.end_turn,
                usage=NormalizedUsage(input_tokens=100, output_tokens=20),
            )
        self.seen_messages.append(list(messages))
        r = self.responses[self.call_count]
        self.call_count += 1
        return r


def _text_reply(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=StopReason.end_turn,
        usage=NormalizedUsage(input_tokens=5, output_tokens=3),
    )


def _make_agent(backend, tmp_path, **overrides) -> Agent:
    config = AgentConfig(model="m", max_turns=5, db_path=str(tmp_path / "runs.db"))
    config.compression.enabled = False
    for k, v in overrides.items():
        setattr(config, k, v)
    return Agent(config, backend)


def _drain(agent: Agent, handle) -> None:
    for _ in handle:
        pass


def test_chat_first_turn_creates_chat_run(tmp_path) -> None:
    backend = _SmartFakeBackend([_text_reply("first reply")])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    assert agent.in_chat is True
    events = agent._chat_traj.events
    run_start = next(e for e in events if e.type == EventType.run_start)
    assert run_start.payload.get("mode") == "chat"
    assert any(e.type == EventType.llm_response for e in events)
    assert not any(e.type == EventType.run_end for e in events)


def test_chat_second_turn_continues_context(tmp_path) -> None:
    backend = _SmartFakeBackend([_text_reply("first"), _text_reply("second")])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    _drain(agent, agent.chat("and then?", str(tmp_path)))
    assert backend.call_count == 2
    second_messages = backend.seen_messages[1]
    texts = [m.content[0].text for m in second_messages if m.role in ("user", "assistant") and getattr(m.content[0], "text", None)]
    assert "hello" in texts
    assert "and then?" in texts
    assert "first" in texts
    turn_starts = [e for e in agent._chat_traj.events if e.type == EventType.turn_start]
    assert [e.turn for e in turn_starts] == [0, 1]


def test_chat_end_emits_run_end_and_allows_new_session(tmp_path) -> None:
    backend = _SmartFakeBackend([_text_reply("one"), _text_reply("two")])
    agent = _make_agent(backend, tmp_path)
    first_handle = agent.chat("hello", str(tmp_path))
    _drain(agent, first_handle)
    first_run_id = agent._chat_traj.run_id
    agent.chat_end()
    assert agent.in_chat is False
    assert any(e.type == EventType.run_end for e in Trajectory.from_db(first_run_id, str(tmp_path / "runs.db")).events)
    _drain(agent, agent.chat("new session", str(tmp_path)))
    assert agent._chat_traj.run_id != first_run_id


def test_chat_resume_restores_history_and_turn(tmp_path) -> None:
    backend = _SmartFakeBackend([_text_reply("first")])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    run_id = agent._chat_traj.run_id
    agent.chat_end()

    backend2 = _SmartFakeBackend([_text_reply("resumed reply"), _text_reply("next reply")])
    agent2 = _make_agent(backend2, tmp_path)
    _drain(agent2, agent2.chat_resume(run_id))
    assert agent2.in_chat is True
    resumed_messages = backend2.seen_messages[0]
    texts = [m.content[0].text for m in resumed_messages if m.role == "user" and getattr(m.content[0], "text", None)]
    assert "hello" in texts
    turn_starts = [e for e in agent2._chat_traj.events if e.type == EventType.turn_start]
    assert turn_starts[-1].turn == 1

    _drain(agent2, agent2.chat("next after resume", str(tmp_path)))
    assert backend2.call_count == 2
    events_after = list(agent2._chat_traj.events)
    agent2.chat_end()
    assert any(e.type == EventType.run_end for e in events_after)


def test_chat_resume_restores_all_user_turns(tmp_path) -> None:
    """B1: multi-turn user messages must survive chat_resume replay.

    Regression: user turns 2+ were never emitted to the trajectory, so a
    resumed session lost them AND produced consecutive assistant messages.
    """
    backend = _SmartFakeBackend([_text_reply("first"), _text_reply("second")])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    _drain(agent, agent.chat("and then?", str(tmp_path)))
    run_id = agent._chat_traj.run_id
    agent.chat_end()

    backend2 = _SmartFakeBackend([_text_reply("resumed reply")])
    agent2 = _make_agent(backend2, tmp_path)
    _drain(agent2, agent2.chat_resume(run_id))
    resumed_messages = backend2.seen_messages[0]
    texts = [
        b.text for m in resumed_messages
        for b in m.content if isinstance(b, TextBlock)
    ]
    assert "hello" in texts
    assert "and then?" in texts
    roles = [m.role for m in resumed_messages]
    for a, b in zip(roles, roles[1:]):
        assert not (a == "assistant" and b == "assistant")


def test_chat_recovers_dangling_tool_use_after_interrupt(tmp_path) -> None:
    """B3: 中断于工具执行中后续聊，悬挂 tool_use 被补执行而非带进 API。"""
    backend = _SmartFakeBackend([_text_reply("after")])
    agent = _make_agent(backend, tmp_path)
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="task"),
        Message(role="assistant", content=[
            ToolUseBlock(id="c1", name="read_file", input={"path": "a.py"})
        ]),
    ]
    agent._chat_messages = msgs
    agent._chat_traj = Trajectory(run_id="y" * 12, config=agent.config)
    agent._chat_bound_tools = {}
    agent._tool_specs = []
    agent._chat_turn = 1

    _drain(agent, agent.chat("继续", str(tmp_path)))

    results_msg = agent._chat_messages[-2]
    assert results_msg.role == "user"
    blob = " ".join(
        (b.content if isinstance(b, ToolResultBlock) else getattr(b, "text", ""))
        for b in results_msg.content
    )
    assert "Unknown tool: read_file" in blob  # 补执行的结果已回填
    assert "继续" in blob  # 新文本合并进结果消息
    seen_texts = [
        b.text for m in backend.seen_messages[0]
        for b in m.content if isinstance(b, TextBlock)
    ]
    assert "继续" in seen_texts
    user_message_evs = [
        e for e in agent._chat_traj.events if e.type == EventType.user_message
    ]
    assert any(e.payload.get("text") == "继续" for e in user_message_evs)


def test_chat_resume_completes_dangling_tools(tmp_path) -> None:
    """B3: 从 DB 恢复时（上次中断于工具执行），悬挂 tool_use 被补执行。"""
    backend = _SmartFakeBackend([_text_reply("first")])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    run_id = agent._chat_traj.run_id
    agent._chat_traj.emit(
        EventType.user_message, turn=1, payload={"text": "继续"},
    )
    agent._chat_traj.emit(EventType.llm_response, turn=1, payload={
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{
            "type": "tool_use", "id": "c1",
            "name": "read_file", "input": {"path": "a.py"},
        }],
    })
    agent.chat_end()

    backend2 = _SmartFakeBackend([_text_reply("resumed")])
    agent2 = _make_agent(backend2, tmp_path)
    _drain(agent2, agent2.chat_resume(run_id))
    resumed = backend2.seen_messages[0]
    roles = [m.role for m in resumed]
    for a, b in zip(roles, roles[1:]):
        assert not (a == "assistant" and b == "assistant")
    tail_blocks = [b for m in resumed[-2:] for b in m.content]
    assert any(
        isinstance(b, ToolResultBlock) and b.tool_use_id == "c1"
        for b in tail_blocks
    )


def test_chat_max_tokens_keeps_partial_reply(tmp_path) -> None:
    """B5: max_tokens 终止时部分回复仍进入会话上下文，可继续对话。"""
    backend = _SmartFakeBackend([
        ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text="partial reply")]),
            stop_reason=StopReason.max_tokens,
            usage=NormalizedUsage(input_tokens=5, output_tokens=3),
        ),
        _text_reply("continued"),
    ])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    _drain(agent, agent.chat("go on", str(tmp_path)))
    second_messages = backend.seen_messages[1]
    texts = [
        b.text for m in second_messages
        for b in m.content if isinstance(b, TextBlock)
    ]
    assert any("partial" in t for t in texts)
    assert any("go on" in t for t in texts)


def test_chat_resume_requires_no_active_session(tmp_path) -> None:
    backend = _SmartFakeBackend([_text_reply("x")])
    agent = _make_agent(backend, tmp_path)
    _drain(agent, agent.chat("hello", str(tmp_path)))
    run_id = agent._chat_traj.run_id
    import pytest
    with pytest.raises(ValueError, match="chat_end"):
        agent.chat_resume(run_id)


def test_compact_chat_requires_active_session(tmp_path) -> None:
    import pytest

    backend = _SmartFakeBackend([])
    agent = _make_agent(backend, tmp_path)
    with pytest.raises(ValueError, match="没有活动会话"):
        agent.compact_chat()


def test_compact_chat_summarizes_older_turns(tmp_path) -> None:
    backend = _SmartFakeBackend(
        [_text_reply(f"reply {i}") for i in range(8)],
        summary_text="[Prior turns summarized.]",
    )
    agent = _make_agent(backend, tmp_path)
    for i in range(8):
        _drain(agent, agent.chat(f"msg {i}", str(tmp_path)))
    before_turns = len(agent._chat_messages)
    summary_calls_before = backend.summary_count

    result = agent.compact_chat()

    assert backend.summary_count == summary_calls_before + 1
    assert result["affected"] > 0
    assert result["before"] > result["after"]
    # ADR-0036：compact 返回摘要文本（供 TUI 展示）
    assert result["summary"] == "[Prior turns summarized.]"
    # system + 摘要 + 最近保留轮
    roles = [m.role for m in agent._chat_messages]
    assert roles[0] == "system"
    assert any("会话摘要" in b.text for m in agent._chat_messages for b in m.content if isinstance(b, TextBlock))
    assert len(agent._chat_messages) < before_turns
    # 轨迹事件 + 落库
    events = agent._chat_traj.events
    comp = [e for e in events if e.type == EventType.compression]
    assert any(e.payload.get("layer") == "auto_compact" for e in comp)
    assert Trajectory.from_db(agent._chat_traj.run_id, str(tmp_path / "runs.db")).events


def test_compact_chat_keeps_recent_turns(tmp_path) -> None:
    backend = _SmartFakeBackend(
        [_text_reply(f"reply {i}") for i in range(6)],
        summary_text="[Prior turns summarized.]",
    )
    agent = _make_agent(backend, tmp_path)
    for i in range(6):
        _drain(agent, agent.chat(f"msg {i}", str(tmp_path)))
    agent.compact_chat(keep_turns=2)
    texts = [
        m.content[0].text
        for m in agent._chat_messages
        if m.role == "user" and isinstance(m.content[0], TextBlock)
    ]
    assert "msg 5" in texts
    assert "msg 4" in texts
    assert "msg 3" not in texts


def test_compact_chat_failure_keeps_messages(tmp_path) -> None:
    class _FailingBackend:
        def complete(self, messages, tools=None, config=None):
            raise RuntimeError("boom")

    agent = _make_agent(_FailingBackend(), tmp_path)
    # chat 首轮会走 backend.complete → 直接手动构造消息
    from vague_code.agent.ir import ToolUseBlock, ToolResultBlock
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="u1"),
        Message(role="assistant", content=[ToolUseBlock(id="c1", name="read_file", input={"path": "a"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="c1", content="data")]),
        Message(role="user", content="u2"),
        Message(role="assistant", content=[TextBlock(text="a2")]),
        Message(role="user", content="u3"),
    ]
    agent._chat_messages = msgs
    agent._chat_traj = Trajectory(run_id="x" * 12, config=agent.config)
    agent._chat_bound_tools = {}
    agent._tool_specs = []

    result = agent.compact_chat()
    assert result["affected"] == 0
    assert len(agent._chat_messages) == len(msgs)

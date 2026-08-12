"""web_search 工具测试（plans/0019：DDG 后端 + network 权限 + 动态注入）。"""

from __future__ import annotations


from vague_code.agent.config import AgentConfig
from vague_code.agent.ir import ToolUseBlock
from vague_code.agent.tools.web_search import MAX_RESULTS_LIMIT, WebSearchTool


def _fake_results(n: int):
    return [
        {"title": f"标题 {i}", "href": f"https://example.com/{i}", "body": f"摘要 {i}"}
        for i in range(n)
    ]


def test_search_formats_results(monkeypatch) -> None:
    class _FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def text(self, query, max_results):
            return _fake_results(2)

    monkeypatch.setattr("ddgs.DDGS", _FakeDDGS)
    r = WebSearchTool(".")({"query": "python"})
    assert "1. 标题 0" in r.output
    assert "https://example.com/0" in r.output
    assert "摘要 0" in r.output


def test_search_no_results(monkeypatch) -> None:
    class _FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def text(self, query, max_results):
            return []

    monkeypatch.setattr("ddgs.DDGS", _FakeDDGS)
    r = WebSearchTool(".")({"query": "zzz"})
    assert "未找到" in r.output


def test_search_failure_returns_guidance(monkeypatch) -> None:
    class _FakeDDGS:
        def __init__(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr("ddgs.DDGS", _FakeDDGS)
    r = WebSearchTool(".")({"query": "x"})
    assert "搜索失败" in r.output


def test_search_empty_query() -> None:
    r = WebSearchTool(".")({"query": ""})
    assert "需要提供搜索查询" in r.output


def test_search_max_results_clamped(monkeypatch) -> None:
    seen: dict = {}

    class _FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def text(self, query, max_results):
            seen["max"] = max_results
            return _fake_results(max_results)

    monkeypatch.setattr("ddgs.DDGS", _FakeDDGS)
    WebSearchTool(".")({"query": "x", "max_results": 999})
    assert seen["max"] == MAX_RESULTS_LIMIT
    WebSearchTool(".")({"query": "x", "max_results": 0})  # 0 = 视为未提供 → 默认
    assert seen["max"] == 5


def test_search_uses_default_max_results(monkeypatch) -> None:
    seen: dict = {}

    class _FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def text(self, query, max_results):
            seen["max"] = max_results
            return _fake_results(max_results)

    monkeypatch.setattr("ddgs.DDGS", _FakeDDGS)
    WebSearchTool(".")({"query": "x"})
    assert seen["max"] == 5  # WebSearchConfig.max_results 默认


def test_metadata_permission_and_scope() -> None:
    tool = WebSearchTool(".")
    assert tool.permission == "network"
    s = tool.resource_scope(ToolUseBlock(id="c1", name="web_search", input={"query": "x"}).input)
    from vague_code.agent.tools.base import OpType, ScopeType
    assert (s.op_type, s.scope_type) == (OpType.READ, ScopeType.WORKSPACE)


def test_dynamic_injection_into_bound_tools(tmp_path) -> None:
    """loop _init_run 按 config.web_search.enabled 注入。"""
    from vague_code.agent.backend import ModelBackend
    from vague_code.agent.ir import Message, NormalizedUsage, StopReason
    from vague_code.agent.loop import Agent

    class _Backend(ModelBackend):
        def complete(self, messages, tools=None, config=None):
            return type("R", (), {"message": Message(role="assistant", content="ok"), "stop_reason": StopReason.end_turn, "usage": NormalizedUsage()})()

    config = AgentConfig(max_turns=5)
    config.memory.enabled = False
    config.repo_map.enabled = False
    agent = Agent(config, _Backend())
    traj, _, bound = agent._init_run("task", str(tmp_path))
    assert "web_search" in bound
    assert any(s.name == "web_search" for s in agent._tool_specs)

    config.web_search.enabled = False
    agent2 = Agent(config, _Backend())
    traj, _, bound2 = agent2._init_run("task", str(tmp_path))
    assert "web_search" not in bound2

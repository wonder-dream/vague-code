from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from src.agent.config import AgentConfig, TransportConfig
from src.agent.ir import (
    Block,
    Message,
    MessageEnd,
    MessageStart,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
    TextDelta,
    ToolSpec,
    ToolUseBlock,
)
from src.agent.loop import Agent
from src.agent.tools import DEFAULT_TOOLS, Tool
from src.agent.trajectory import Event, EventType, Trajectory
from src.agent.backend import DeepSeekBackend


class FakeBackend:
    def __init__(self, responses: list[ModelResponse | Exception]):
        self.responses = responses
        self.call_count = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse:
        r = self.responses[self.call_count]
        self.call_count += 1
        if isinstance(r, Exception):
            raise r
        return r


def _text_response(text: str, stop_reason: StopReason = StopReason.end_turn) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=stop_reason,
        usage=NormalizedUsage(input_tokens=5, output_tokens=3),
    )


def _tool_use_response(*tools: tuple[str, str, dict]) -> ModelResponse:
    blocks: list[Block] = []
    for tid, name, input_ in tools:
        blocks.append(ToolUseBlock(id=tid, name=name, input=input_))
    return ModelResponse(
        message=Message(role="assistant", content=blocks),
        stop_reason=StopReason.tool_use,
        usage=NormalizedUsage(input_tokens=10, output_tokens=5),
    )


# ── Happy path ──────────────────────────────────────────────────────────


def test_single_turn_end_turn():
    backend = FakeBackend([_text_response("Hello!")])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("Say hi", str(Path.cwd()))

    assert traj.run_id
    types = [e.type for e in traj.events]
    assert types[0] == "run_start"
    assert "turn_start" in types
    assert "llm_response" in types
    assert types[-1] == "run_end"

    run_end = traj.events[-1]
    assert run_end.payload["reason"] == "end_turn"
    assert backend.call_count == 1


def test_start_includes_system_message():
    backend = FakeBackend([_text_response("ok")])
    config = AgentConfig(max_turns=5)
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = Agent(config, backend)
        traj = agent.run("test", tmpdir)

    msgs = traj.to_messages()
    assert len(msgs) >= 2
    assert msgs[0].role == "system"
    assert "You are Xcode" in msgs[0].content[0].text
    assert msgs[1].role == "user"


def test_token_budget_recorded():
    backend = FakeBackend([_text_response("ok")])
    config = AgentConfig(max_turns=5)
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = Agent(config, backend)
        traj = agent.run("test", tmpdir)

    compressions = [e for e in traj.events if e.type == EventType.compression]
    assert len(compressions) >= 1
    assert compressions[0].payload["layer"] == "budget"
    assert compressions[0].payload["budget"] > 0
    assert "utilization" in compressions[0].payload


def test_multi_turn_tool_use_then_end_turn():
    backend = FakeBackend([
        _tool_use_response(("call_1", "read_file", {"path": "README.md"})),
        _text_response("Here is the content."),
    ])
    config = AgentConfig(max_turns=5)

    with tempfile.TemporaryDirectory() as tmpdir:
        readme = Path(tmpdir) / "README.md"
        readme.write_text("# Project", encoding="utf-8")
        agent = Agent(config, backend)
        traj = agent.run("Read README", tmpdir)

    types = [e.type for e in traj.events]
    assert "turn_start" in types
    assert "tool_call" in types
    assert "tool_result" in types
    assert traj.events[-1].payload["reason"] == "end_turn"
    assert backend.call_count == 2


def test_max_turns_meltdown():
    backend = FakeBackend([
        _tool_use_response(("call_1", "read_file", {"path": "x.txt"})),
    ])
    config = AgentConfig(max_turns=1)

    with tempfile.TemporaryDirectory() as tmpdir:
        agent = Agent(config, backend)
        traj = agent.run("do stuff", tmpdir)

    assert traj.events[-1].payload["reason"] == "max_turns"
    assert traj.events[-1].payload.get("pending_tool_calls") == 1
    tool_results = [e for e in traj.events if e.type == "tool_result"]
    assert len(tool_results) == 0


# ── Boundary ────────────────────────────────────────────────────────────


def test_max_turns_one_tool_not_executed():
    backend = FakeBackend([
        _tool_use_response(("c1", "read_file", {"path": "x.txt"})),
    ])
    config = AgentConfig(max_turns=1)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert traj.events[-1].payload["reason"] == "max_turns"
    assert traj.events[-1].payload["pending_tool_calls"] == 1
    assert not any(e.type == "tool_result" for e in traj.events)
    assert backend.call_count == 1


def test_max_turns_zero_raises():
    with pytest.raises(ValueError, match="max_turns"):
        AgentConfig(max_turns=0)


def test_max_turns_negative_raises():
    with pytest.raises(ValueError, match="max_turns"):
        AgentConfig(max_turns=-1)


def test_transport_zero_timeout_raises():
    with pytest.raises(ValueError, match="timeout_s"):
        TransportConfig(timeout_s=0)


def test_transport_negative_timeout_raises():
    with pytest.raises(ValueError, match="timeout_s"):
        TransportConfig(timeout_s=-5)


def test_config_empty_model_raises():
    with pytest.raises(ValueError, match="model"):
        AgentConfig(model="")


def test_config_whitespace_model_raises():
    with pytest.raises(ValueError, match="model"):
        AgentConfig(model="   ")


def test_config_empty_db_path_raises():
    with pytest.raises(ValueError, match="db_path"):
        AgentConfig(db_path="")


def test_config_model_with_newline_raises():
    with pytest.raises(ValueError, match="invalid"):
        AgentConfig(model="deepseek\nv4")


def test_config_model_with_spaces_raises():
    with pytest.raises(ValueError, match="invalid"):
        AgentConfig(model="deep seek")


def test_config_db_path_non_db_extension_raises():
    with pytest.raises(ValueError, match="db_path"):
        AgentConfig(db_path="/tmp/not_a_db.txt")


def test_config_high_max_turns_warns():
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        AgentConfig(max_turns=10000)
        assert len(w) == 1
        assert "unusually high" in str(w[0].message)


def test_max_tokens_termination():
    backend = FakeBackend([_text_response("partial", stop_reason=StopReason.max_tokens)])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert traj.events[-1].payload["reason"] == "max_tokens"


def test_unknown_stop_reason_termination():
    backend = FakeBackend([_text_response("?", stop_reason=StopReason.unknown)])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert traj.events[-1].payload["reason"] == "unknown"


def test_content_filter_termination():
    backend = FakeBackend([_text_response("", stop_reason=StopReason.content_filter)])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert traj.events[-1].payload["reason"] == "content_filter"


def test_empty_content_response():
    backend = FakeBackend([_text_response("")])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert traj.events[-1].payload["reason"] == "end_turn"


# ── Exception injection ─────────────────────────────────────────────────


class _FakeTimeoutBackend:
    def complete(self, messages, tools=None, config=None) -> ModelResponse:
        import httpx
        from openai import APITimeoutError
        raise APITimeoutError(request=httpx.Request("GET", "https://api.example.com"))


def test_backend_timeout():
    backend = _FakeTimeoutBackend()
    config = AgentConfig(max_turns=5, transport=TransportConfig(retry_enabled=False))
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert any(e.type == "error" for e in traj.events)
    error = [e for e in traj.events if e.type == "error"][0]
    assert error.payload["kind"] == "llm_timeout"
    assert traj.events[-1].payload["reason"] == "llm_timeout"


def _make_api_error(message: str = "api error") -> Exception:
    import httpx
    from openai import APIError
    return APIError(message, request=httpx.Request("GET", "https://api.example.com"), body=None)


class _FakeAPIErrorBackend:
    def complete(self, messages, tools=None, config=None) -> ModelResponse:
        raise _make_api_error("bad request")


def test_backend_api_error():
    backend = _FakeAPIErrorBackend()
    config = AgentConfig(max_turns=5, transport=TransportConfig(retry_enabled=False))
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert any(e.type == "error" for e in traj.events)
    error = [e for e in traj.events if e.type == "error"][0]
    assert error.payload["kind"] == "llm_error"
    assert traj.events[-1].payload["reason"] == "llm_error"


def test_tool_execution_exception_sets_is_error():
    backend = FakeBackend([
        _tool_use_response(("c1", "read_file", {"path": "/nonexistent/file.txt"})),
        _text_response("Got an error"),
    ])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    tool_results = [e for e in traj.events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True
    assert traj.events[-1].payload["reason"] == "end_turn"


def test_unknown_tool_name():
    backend = FakeBackend([
        ModelResponse(
            message=Message(role="assistant", content=[ToolUseBlock(id="c1", name="unknown_tool", input={})]),
            stop_reason=StopReason.tool_use,
            usage=NormalizedUsage(input_tokens=5, output_tokens=2),
        ),
        _text_response("okay"),
    ])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    tool_results = [e for e in traj.events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].payload["is_error"] is True


# ── Verification ────────────────────────────────────────────────────────


def test_agent_config_has_no_sensitive_fields():
    from dataclasses import fields
    assert "api_key" not in {f.name for f in fields(AgentConfig)}
    assert "base_url" not in {f.name for f in fields(AgentConfig)}


def test_to_messages_with_errors_does_not_crash():
    class _CrashBackend:
        def complete(self, messages, tools=None, config=None) -> ModelResponse:
            raise _make_api_error("crash")

    backend = _CrashBackend()
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")
    msgs = traj.to_messages()
    assert len(msgs) > 0


def test_jsonl_export_format():
    backend = FakeBackend([_text_response("ok")])
    config = AgentConfig(max_turns=5)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = f.name
        traj.export_jsonl(path)

    lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == len(traj.events)


# ── Tool registry (ADR-0004) ─────────────────────────────────────────────


def test_empty_tools_registry():
    called_with: list = []

    class _RecordingBackend:
        def complete(self, messages, tools=None, config=None) -> ModelResponse:
            called_with.append(tools)
            return _text_response("ok")

    agent = Agent(AgentConfig(max_turns=5), _RecordingBackend(), tools={})
    assert agent._tool_specs == []
    agent.run("x", ".")
    assert called_with[-1] == []


def test_bind_failure_emits_tool_bind_error():
    def _broken_factory(workdir: str) -> None:
        raise RuntimeError("bind failed")

    broken_spec = ToolSpec(
        name="broken", description="broken", parameters={"type": "object", "properties": {}}
    )
    broken_tool = Tool(spec=broken_spec, factory=_broken_factory)  # type: ignore[arg-type]
    agent = Agent(AgentConfig(max_turns=5), FakeBackend([]), tools={"broken": broken_tool})
    traj = agent.run("x", ".")
    assert any(e.type == EventType.error for e in traj.events)
    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "tool_bind_error"
    assert traj.events[-1].payload["reason"] == "tool_bind_error"


def test_empty_tool_use_emits_error():
    resp = ModelResponse(
        message=Message(role="assistant", content=""),
        stop_reason=StopReason.tool_use,
        usage=NormalizedUsage(input_tokens=5, output_tokens=2),
    )
    agent = Agent(AgentConfig(max_turns=5), FakeBackend([resp]))
    traj = agent.run("x", ".")
    assert any(e.type == EventType.error for e in traj.events)
    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "empty_tool_use"
    assert traj.events[-1].payload["reason"] == "empty_tool_use"


def test_tool_registry_key_name_mismatch_raises():
    spec = ToolSpec(name="correct_name", description="x", parameters={"type": "object", "properties": {}})
    with pytest.raises(ValueError, match="Registry key.*does not match"):
        Agent(AgentConfig(), FakeBackend([]), tools={"wrong_key": Tool(spec=spec, factory=lambda wd: lambda inp: "")})


# ── T6: path traversal ─────────────────────────────────────────────────


def test_read_file_path_traversal_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    (ws2 / "secret.txt").write_text("TOP SECRET")

    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(PermissionError):
        handler({"path": "../ws2/secret.txt"})


def test_read_file_rejects_null_byte_path(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": "foo\x00.txt"})


def test_read_file_truncates_large_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    big_file = ws / "big.txt"
    big_file.write_text("A" * 2000, encoding="utf-8")
    import src.agent.tools as tmod
    original_max = tmod.MAX_READ_BYTES
    try:
        tmod.MAX_READ_BYTES = 1000
        handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
        result = handler({"path": "big.txt"})
        assert len(result) > 1000
        assert "truncated" in result
    finally:
        tmod.MAX_READ_BYTES = original_max


def test_read_file_strips_utf8_bom(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bom.txt").write_bytes(b'\xef\xbb\xbf{"key": "value"}')
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    result = handler({"path": "bom.txt"})
    assert result.startswith('{"key"')
    assert "\ufeff" not in result


def test_read_file_null_path_diagnostic(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": None})


# ── T1: export_jsonl full event per line ───────────────────────────────


def test_export_jsonl_full_event_per_line(tmp_path):
    traj = Agent(AgentConfig(), FakeBackend([_text_response("ok")])).run("x", ".")
    path = tmp_path / "t.jsonl"
    traj.export_jsonl(path)

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == len(traj.events)
    for line, ev in zip(lines, traj.events):
        d = json.loads(line)
        assert isinstance(d, dict)
        assert d["run_id"] == traj.run_id
        assert d["type"] == ev.type.value
        assert set(d) >= {"run_id", "turn", "ts", "type", "payload"}
        assert d["payload"] == ev.payload


# ── T3+T4: persist idempotency + supplement ────────────────────────────


def test_persist_is_idempotent(tmp_path):
    config = AgentConfig(db_path=str(tmp_path / "t.db"))
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    traj = agent.run("x", ".")
    traj.persist()

    import sqlite3
    conn = sqlite3.connect(config.db_path)
    n = conn.execute("SELECT count(*) FROM events WHERE run_id=?", (traj.run_id,)).fetchone()[0]
    conn.close()
    assert n == len(traj.events)


def test_persist_failure_does_not_duplicate_run_end(monkeypatch, tmp_path):
    import sqlite3 as _sqlite3
    config = AgentConfig(db_path=str(tmp_path / "t.db"))

    fail = True
    _orig_connect = _sqlite3.connect
    def flaky_connect(*a, **kw):
        if fail:
            raise OSError("disk full")
        return _orig_connect(*a, **kw)

    monkeypatch.setattr("sqlite3.connect", flaky_connect)
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    traj = agent.run("x", ".")
    assert sum(1 for e in traj.events if e.type == EventType.run_end) == 1
    assert any(e.type == EventType.run_end and e.payload["reason"] == "end_turn" for e in traj.events)

    fail = False
    traj.persist()
    conn = _sqlite3.connect(config.db_path)
    n = conn.execute("SELECT count(*) FROM events WHERE run_id=?", (traj.run_id,)).fetchone()[0]
    conn.close()
    assert n == len(traj.events)


# ── B4: loop exception catch-all + recovery ────────────────────────────


def test_agent_loop_catches_non_api_exceptions(tmp_path):
    class _ValueErrorBackend:
        def complete(self, messages, tools=None, config=None):
            raise ValueError("codec exploded")

    config = AgentConfig(max_turns=5, db_path=str(tmp_path / "t.db"), transport=TransportConfig(retry_enabled=False))
    agent = Agent(config, _ValueErrorBackend())
    traj = agent.run("x", ".")

    assert any(e.type == EventType.error for e in traj.events)
    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "codec_error"
    assert error.payload["message"] == "codec exploded"
    assert traj.events[-1].payload["reason"] == "llm_error"


def test_persist_failure_writes_recovery_jsonl(tmp_path, monkeypatch):
    import sqlite3 as _sqlite3

    config = AgentConfig(db_path=str(tmp_path / "test.db"))
    _orig_connect = _sqlite3.connect

    def permfail_connect(*a, **kw):
        raise PermissionError("no write access")

    monkeypatch.setattr("sqlite3.connect", permfail_connect)
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    traj = agent.run("x", ".")
    recovery = tmp_path / f"test.{traj.run_id}.recovery.jsonl"
    assert recovery.exists()
    lines = recovery.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == len(traj.events)


# ── T2: to_messages ordering ───────────────────────────────────────────


def test_to_messages_preserves_turn_interleaving(tmp_path):
    (tmp_path / "a.txt").write_text("file body")
    backend = FakeBackend([
        _tool_use_response(("c1", "read_file", {"path": "a.txt"})),
        _text_response("done"),
    ])
    traj = Agent(AgentConfig(max_turns=5), backend).run("x", str(tmp_path))

    msgs = traj.to_messages()
    seq = [(m.role, type(m.content[0]).__name__) for m in msgs]
    assert seq == [
        ("system", "TextBlock"),
        ("user", "TextBlock"),
        ("assistant", "ToolUseBlock"),
        ("user", "ToolResultBlock"),
        ("assistant", "TextBlock"),
    ]


def test_sqlite_persist():
    backend = FakeBackend([_text_response("ok")])
    config = AgentConfig(max_turns=5)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        config.db_path = db_path
        agent = Agent(config, backend)
        traj = agent.run("x", ".")
        assert Path(db_path).exists()
        import sqlite3
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        assert rows == len(traj.events)
        run = conn.execute("SELECT * FROM runs").fetchone()
        assert run[0] == traj.run_id
        conn.close()


# ── B5: trajectory robustness ──────────────────────────────────────────


def test_emit_payload_not_mutated_by_caller():
    traj = Trajectory(run_id="test", config=AgentConfig())
    payload = {"key": "value"}
    traj.emit(EventType.run_start, payload=payload)
    payload["key"] = "mutated"
    assert traj.events[0].payload["key"] == "value"


def test_sqlite_persist_uses_wal_mode(tmp_path):
    config = AgentConfig(db_path=str(tmp_path / "test.db"))
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    agent.run("x", ".")
    import sqlite3
    conn = sqlite3.connect(config.db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_to_messages_with_non_dict_block_skipped():
    traj = Trajectory(run_id="test", config=AgentConfig())
    traj.emit(EventType.run_start, payload={"task": "hi"})
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "stop",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{"type": "text", "text": "hello"}, "not_a_dict"],
    })
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    msgs = traj.to_messages()
    assert len(msgs) == 2
    assert len(msgs[1].content) == 1
    assert isinstance(msgs[1].content[0], TextBlock)


# ── B6: backend config safety ──────────────────────────────────────────


def test_backend_config_not_dict_uses_default_model():
    class _FakeCompletions:
        captured_body = None

        @staticmethod
        def create(**body):
            _FakeCompletions.captured_body = body

            class _FakeRaw:
                @staticmethod
                def model_dump(**kw):
                    return {
                        "choices": [{"message": {"role": "assistant", "content": "ok"},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }
            return _FakeRaw()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    backend = DeepSeekBackend(api_key="sk-test", timeout_s=5)
    backend._client = _FakeClient()
    result = backend.complete(
        [Message(role="user", content="hi")],
        config=42,  # non-dict
    )
    assert _FakeCompletions.captured_body["model"] == "deepseek-v4-flash"
    assert result.stop_reason == StopReason.end_turn


# ── Adversarial: Round-2 HIGH severity ─────────────────────────────────


def test_to_messages_survives_malformed_tool_result_id():
    """#3: tool_result block with empty tool_use_id must NOT crash to_messages()."""
    traj = Trajectory(run_id="test", config=AgentConfig())
    traj.emit(EventType.run_start, payload={"task": "hi"})
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{"type": "text", "text": "calling tool..."}],
    })
    traj.emit(EventType.tool_result, turn=0, payload={
        "tool_use_id": "",
        "content": "result",
        "is_error": False,
    })
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    msgs = traj.to_messages()
    assert len(msgs) >= 1


def test_recovery_jsonl_does_not_overwrite_prior_run(tmp_path, monkeypatch):
    """#8: 同 db_path 的两次 run 产生独立 recovery 文件，互不覆盖。"""
    import sqlite3 as _sqlite3

    config = AgentConfig(db_path=str(tmp_path / "test.db"))
    _orig_connect = _sqlite3.connect

    def permfail_connect(*a, **kw):
        raise PermissionError("no write access")

    monkeypatch.setattr("sqlite3.connect", permfail_connect)

    agent1 = Agent(config, FakeBackend([_text_response("ok")]))
    traj1 = agent1.run("task1", ".")
    recovery1 = tmp_path / f"test.{traj1.run_id}.recovery.jsonl"
    assert recovery1.exists()

    agent2 = Agent(config, FakeBackend([_text_response("ok")]))
    traj2 = agent2.run("task2", ".")
    recovery2 = tmp_path / f"test.{traj2.run_id}.recovery.jsonl"
    assert recovery2.exists()

    assert traj1.run_id != traj2.run_id
    assert recovery1 != recovery2
    assert recovery1.read_text(encoding="utf-8") != recovery2.read_text(encoding="utf-8")


def test_jsonl_lines_equal_event_count(tmp_path):
    """#10: JSONL 行数应等于事件数，U+2028/U+2029 被转义后不破坏结构。"""
    traj = Trajectory(run_id="test", config=AgentConfig())
    evil_content = "line1\u2028line2\u2029line3"
    traj.emit(EventType.run_start, payload={"task": "hi"})
    traj.emit(EventType.tool_result, turn=0, payload={
        "tool_use_id": "c1",
        "content": evil_content,
        "is_error": False,
    })
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})

    path = tmp_path / "fixed.jsonl"
    traj.export_jsonl(path)
    raw = path.read_text(encoding="utf-8")
    lines = raw.strip().split("\n")

    assert len(traj.events) == 3
    assert len(lines) == len(traj.events)
    assert "\u2028" not in raw
    assert "\\u2028" in raw


# ── B12: trajectory defense ────────────────────────────────────────────


def test_to_messages_deduplicates_consecutive_run_starts():
    traj = Trajectory(run_id="test", config=AgentConfig())
    traj.emit(EventType.run_start, payload={"task": "first"})
    traj.emit(EventType.run_start, payload={"task": "second"})
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "stop",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{"type": "text", "text": "ok"}],
    })
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    msgs = traj.to_messages()
    assert len(msgs) == 2


def test_event_to_row_handles_unserializable_payload():
    ev = Event(run_id="t", turn=0, ts=0, type=EventType.run_start, payload={"fn": lambda: None})
    row = ev.to_row()
    d = json.loads(row[4])
    assert isinstance(d["fn"], str)


# ── Retry behavior ──────────────────────────────────────────────────────────


def _rate_limit_error() -> Exception:
    import httpx
    from openai import RateLimitError
    resp = httpx.Response(429, request=httpx.Request("POST", "https://api.example.com/v1"))
    return RateLimitError("rate limit", response=resp, body=None)


def test_retry_success(monkeypatch):
    backend = FakeBackend([_rate_limit_error(), _text_response("ok")])
    config = AgentConfig(max_turns=5)
    monkeypatch.setattr("time.sleep", lambda _: None)
    import random
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    retry_events = [e for e in traj.events if e.type == EventType.retry]
    assert len(retry_events) == 1
    assert retry_events[0].payload["reason"] == "rate_limit"
    assert retry_events[0].payload["attempt"] == 1

    error_events = [e for e in traj.events if e.type == EventType.error]
    assert len(error_events) == 0

    assert traj.events[-1].payload["reason"] == "end_turn"
    assert backend.call_count == 2

    stream_events = [e for e in traj.events if e.type == EventType.stream_event]
    # only successful attempt's events are written (MessageStart + TextDelta + MessageEnd)
    assert len(stream_events) == 3


def test_retry_disabled(monkeypatch):
    backend = FakeBackend([_rate_limit_error()])
    config = AgentConfig(max_turns=5, transport=TransportConfig(retry_enabled=False))
    monkeypatch.setattr("time.sleep", lambda _: None)
    import random
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert any(e.type == EventType.retry for e in traj.events) is False
    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "rate_limit"
    assert traj.events[-1].payload["reason"] == "llm_error"
    assert backend.call_count == 1


def test_retry_exhausted(monkeypatch):
    backend = FakeBackend([_rate_limit_error(), _rate_limit_error(), _rate_limit_error()])
    config = AgentConfig(max_turns=5, transport=TransportConfig(retry_max_attempts=2))
    monkeypatch.setattr("time.sleep", lambda _: None)
    import random
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    retry_events = [e for e in traj.events if e.type == EventType.retry]
    assert len(retry_events) == 2

    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "retry_exhausted"
    assert error.payload["attempts"] == 2
    assert error.payload["last_error_kind"] == "rate_limit"
    assert traj.events[-1].payload["reason"] == "llm_error"
    assert backend.call_count == 3


def test_retry_non_retryable(monkeypatch):
    import httpx
    from openai import BadRequestError
    req = httpx.Request("POST", "https://api.example.com/v1")
    resp = httpx.Response(400, request=req)
    err = BadRequestError("bad request", response=resp, body=None)
    backend = FakeBackend([err])
    config = AgentConfig(max_turns=5)
    monkeypatch.setattr("time.sleep", lambda _: None)
    import random
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    assert any(e.type == EventType.retry for e in traj.events) is False
    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "llm_error"
    assert backend.call_count == 1


def test_retry_with_retry_notice_in_stream(monkeypatch):
    """RunHandle 产生的实时事件序列包含 RetryNotice, trajectory 无 RetryNotice 但含 retry"""
    import random

    class _StreamRetryBackend:
        def __init__(self):
            self.call_count = 0
        def complete(self, messages, tools=None, config=None):
            self.call_count += 1
            raise AssertionError("should not be called")
        def stream(self, messages, tools=None, config=None):
            self.call_count += 1
            if self.call_count == 1:
                # first stream: yield MessageStart then fail
                yield MessageStart(model="test")
                yield TextDelta(delta="partial")
                import httpx
                from openai import APIConnectionError
                req = httpx.Request("POST", "https://api.example.com/v1")
                raise APIConnectionError(message="stream dropped", request=req)
            # second stream: succeed
            yield MessageStart(model="test")
            yield TextDelta(delta="done")
            yield MessageEnd(stop_reason=StopReason.end_turn, usage=NormalizedUsage(2, 1))

    backend = _StreamRetryBackend()
    config = AgentConfig(max_turns=5, transport=TransportConfig(stream=True))
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    agent = Agent(config, backend)
    handle = agent.start("x", ".")

    collected_types: list[str] = []
    for ev in handle:
        collected_types.append(type(ev).__name__)

    traj = handle.trajectory

    # RunHandle should contain RetryNotice between partials and final
    assert "RetryNotice" in collected_types, f"got event types: {collected_types}"
    retry_idx = collected_types.index("RetryNotice")
    assert retry_idx > 0  # appears after first attempt's deltas
    assert retry_idx < len(collected_types) - 1  # not last

    # trajectory should have retry event but NO RetryNotice
    assert any(e.type == EventType.retry for e in traj.events)
    assert not any(e.type == EventType.stream_event and e.payload.get("stream_type") == "retry_notice" for e in traj.events)
    assert len([e for e in traj.events if e.type == EventType.llm_response]) == 1
    assert traj.events[-1].payload["reason"] == "end_turn"

    stream_events_in_traj = [e for e in traj.events if e.type == EventType.stream_event]
    assert len(stream_events_in_traj) == 3  # MessageStart + TextDelta + MessageEnd


def test_trajectory_emit_with_ts():
    traj = Trajectory(run_id="test", config=AgentConfig())
    traj.emit(EventType.run_start, payload={"task": "hi"}, ts=100.0)
    assert traj.events[0].ts == 100.0


def test_retry_max_attempts_zero(monkeypatch):
    """retry_max_attempts=0 且 retry_enabled=True → 首次异常即 retry_exhausted"""
    import random
    backend = FakeBackend([_rate_limit_error()])
    config = AgentConfig(max_turns=5, transport=TransportConfig(retry_max_attempts=0))
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    agent = Agent(config, backend)
    traj = agent.run("x", ".")

    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "retry_exhausted"
    assert error.payload["attempts"] == 0
    assert backend.call_count == 1


# ── Checkpoint & resume ──────────────────────────────────────────────────────


def test_from_db_roundtrip(tmp_path):
    db_path = str(tmp_path / "test.db")
    config = AgentConfig(max_turns=5, db_path=db_path)
    traj1 = Agent(config, FakeBackend([_text_response("ok")])).run("x", ".")
    traj2 = Trajectory.from_db(traj1.run_id, db_path)

    assert traj2.run_id == traj1.run_id
    assert traj2.config.model == config.model
    assert len(traj2.events) == len(traj1.events)
    for e1, e2 in zip(traj1.events, traj2.events):
        assert e1.type == e2.type
        assert e1.payload == e2.payload


def test_from_db_unknown_run_raises(tmp_path):
    db_path = str(tmp_path / "t.db")
    with pytest.raises(ValueError, match="not found"):
        Trajectory.from_db("nonexistent", db_path)


def test_resume_already_finished_returns_immediately(tmp_path):
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=5, db_path=db_path)
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    traj = agent.run("x", ".")
    result = agent.resume(traj)
    assert result is traj
    assert result.events[-1].payload["reason"] == "end_turn"


def test_resume_pending_tools(tmp_path):
    """Simulate crash after checkpoint: events have llm_response(tool_use) but no tool results.
    Resume should execute pending tools and continue to completion."""
    db_path = str(tmp_path / "checkpoint.db")
    config = AgentConfig(max_turns=5, db_path=db_path)

    traj = Trajectory(run_id="test_crash", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "read file", "workdir": str(tmp_path),
        "config": config.to_public_dict(), "tools": ["read_file"],
    })
    traj.emit(EventType.turn_start, turn=0)
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "blocks": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "x.txt"}}],
    })
    traj.persist()
    del traj

    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")

    agent = Agent(config, FakeBackend([_text_response("done")]), tools=DEFAULT_TOOLS)
    loaded = Trajectory.from_db("test_crash", db_path)
    result = agent.resume(loaded)

    assert result.events[-1].payload["reason"] == "end_turn"
    tool_results = [e for e in result.events if e.type == EventType.tool_result]
    assert len(tool_results) == 1
    assert "hello" in tool_results[0].payload["content"]
    # Turn attribution must match the llm_response's turn (P0 regression guard)
    tool_calls = [e for e in result.events if e.type == EventType.tool_call]
    assert all(e.turn == 0 for e in tool_calls), f"tool_call turn not 0: {[(e.turn, e.payload) for e in tool_calls]}"
    assert all(e.turn == 0 for e in tool_results), f"tool_result turn not 0: {[(e.turn, e.payload) for e in tool_results]}"


def test_checkpoint_persist_does_not_crash(monkeypatch, tmp_path):
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=5, db_path=db_path)
    monkeypatch.setattr("time.sleep", lambda _: None)
    import random
    monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)
    backend = FakeBackend([
        _tool_use_response(("c1", "read_file", {"path": "x.txt"})),
        _text_response("done"),
    ])
    (tmp_path / "x.txt").write_text("content", encoding="utf-8")
    agent = Agent(config, backend)
    traj = agent.run("read file", str(tmp_path))

    assert traj.events[-1].payload["reason"] == "end_turn"
    # Ensure events include tool execution (checkpoint didn't break anything)
    tool_results = [e for e in traj.events if e.type == EventType.tool_result]
    assert len(tool_results) == 1


def test_resume_max_turns_2_not_dead(tmp_path):
    """P0 regression: max_turns=2, crash at T=0 (tool_use with no tools done).
    Resume must still call LLM and reach end_turn, not run_end(max_turns)."""
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=2, db_path=db_path)

    traj = Trajectory(run_id="r2", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "read and answer", "workdir": str(tmp_path),
        "config": config.to_public_dict(), "tools": ["read_file"],
    })
    traj.emit(EventType.turn_start, turn=0)
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "blocks": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "x.txt"}}],
    })
    traj.persist()
    del traj

    (tmp_path / "x.txt").write_text("content", encoding="utf-8")

    agent = Agent(config, FakeBackend([_text_response("answer")]), tools=DEFAULT_TOOLS)
    loaded = Trajectory.from_db("r2", db_path)
    result = agent.resume(loaded)

    # Must reach end_turn, not max_turns (which would mean LLM never called again)
    assert result.events[-1].payload["reason"] == "end_turn"
    # Two llm_responses: turn 0 (crashed) + turn 1 (resumed)
    llms = [e for e in result.events if e.type == EventType.llm_response]
    assert len(llms) == 2, f"Expected 2 llm_responses, got {len(llms)} turns: {[e.turn for e in llms]}"
    assert llms[1].turn == 1


def test_resume_defensive_terminal_replay(tmp_path):
    """Defensive: hand-crafted llm_response(end_turn) without run_end → no extra LLM call."""
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=5, db_path=db_path)

    traj = Trajectory(run_id="r_terminal", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "hi", "workdir": str(tmp_path),
        "config": config.to_public_dict(), "tools": [],
    })
    traj.emit(EventType.turn_start, turn=0)
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{"type": "text", "text": "hello"}],
    })
    traj.persist()
    del traj

    agent = Agent(config, FakeBackend([_text_response("should never be called")]), tools={})
    loaded = Trajectory.from_db("r_terminal", db_path)
    result = agent.resume(loaded)

    assert result.events[-1].payload["reason"] == "end_turn"
    llms = [e for e in result.events if e.type == EventType.llm_response]
    assert len(llms) == 1, f"Expected 1 llm_response, got {len(llms)}"


def test_resume_defensive_meltdown_replay(tmp_path):
    """Defensive: hand-crafted tool_use tail with max_turns=T+1 → run_end(max_turns), tools not executed."""
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=1, db_path=db_path)

    traj = Trajectory(run_id="r_melt", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "do stuff", "workdir": str(tmp_path),
        "config": config.to_public_dict(), "tools": ["read_file"],
    })
    traj.emit(EventType.turn_start, turn=0)
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "blocks": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "x.txt"}}],
    })
    traj.persist()
    del traj

    agent = Agent(config, FakeBackend([_text_response("should never be called")]), tools=DEFAULT_TOOLS)
    loaded = Trajectory.from_db("r_melt", db_path)
    result = agent.resume(loaded)

    assert result.events[-1].payload["reason"] == "max_turns"
    assert result.events[-1].payload.get("pending_tool_calls") == 1
    assert any(e.type == EventType.tool_call for e in result.events) is False


def test_from_db_unknown_fields(tmp_path):
    """from_db with fields not known to current AgentConfig/TransportConfig loads with warning."""
    import warnings
    db_path = str(tmp_path / "t.db")
    import sqlite3
    import json
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, config_json TEXT, status TEXT)")
    config_with_extra = {
        "model": "deepseek-v4-flash", "max_turns": 3,
        "db_path": db_path, "transport": {"stream": True, "unknown_field": 42},
        "turn_timeout_s": 120.0,
    }
    conn.execute("INSERT INTO runs VALUES (?, ?, ?)", ("r1", json.dumps(config_with_extra), "in_progress"))
    conn.execute("CREATE TABLE events (run_id TEXT, turn INTEGER, ts REAL, type TEXT, payload TEXT)")
    conn.commit()
    conn.close()

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        traj = Trajectory.from_db("r1", db_path)
        assert any("AgentConfig fields: turn_timeout_s" in str(m.message) for m in w), [str(m.message) for m in w]
        assert any("TransportConfig fields: unknown_field" in str(m.message) for m in w), [str(m.message) for m in w]
    assert len(traj.events) == 0


def test_tools_mismatch_error(tmp_path):
    """Resume with different tool registry → ValueError."""
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=5, db_path=db_path)

    traj = Trajectory(run_id="r_mismatch", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "x", "workdir": str(tmp_path),
        "config": config.to_public_dict(), "tools": ["read_file", "write_file"],
    })
    traj.persist()
    del traj

    agent = Agent(config, FakeBackend([]), tools={})  # empty registry
    loaded = Trajectory.from_db("r_mismatch", db_path)
    with pytest.raises(ValueError, match="Tool registry mismatch"):
        agent.resume(loaded)


def test_tools_field_missing_warning(tmp_path):
    """Old run_start without 'tools' field → warning, proceeds."""
    import warnings
    db_path = str(tmp_path / "t.db")
    config = AgentConfig(max_turns=5, db_path=db_path)

    traj = Trajectory(run_id="r_old", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "x", "workdir": str(tmp_path), "config": config.to_public_dict(),
    })
    traj.persist()
    del traj

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        agent = Agent(config, FakeBackend([]))
        loaded = Trajectory.from_db("r_old", db_path)
        agent.resume(loaded)
        assert any("without 'tools' field" in str(m.message) for m in w), [str(m.message) for m in w]


# ── E2E (FakeBackend) ────────────────────────────────────────────────────────


TARGET_BUG_DIR = Path(__file__).parent / "_target_bug"


def test_e2e_read_patch_bash_pass():
    """
    End-to-end: Agent 修复全部 5 个 bug（3 visible + 2 hidden）。

    Visible (pytest catches):
      V1  stats.py: pass → continue
      V2  repo.py:  page*size → (page-1)*size
      V3  repo.py:  <= min_rating → >= min_rating

    Hidden (no test covers, only code review catches):
      H1  repo.py:  update() reports True but never updates
      H2  repo.py:  delete() reports True but never deletes
    """
    backend = FakeBackend([
        _tool_use_response(("c1", "read_file", {"path": "src/stats.py"})),
        _tool_use_response(("c2", "read_file", {"path": "src/repo.py"})),
        _tool_use_response(("c3", "patch", {
            "path": "src/stats.py",
            "old_str": "            if p.stock == 0:\n                pass",
            "new_str": "            if p.stock == 0:\n                continue",
        })),
        _tool_use_response(("c4", "patch", {
            "path": "src/repo.py",
            "old_str": "        start = page * page_size  # BUG: off-by-one, page 1 should start at 0",
            "new_str": "        start = (page - 1) * page_size",
        })),
        _tool_use_response(("c5", "patch", {
            "path": "src/repo.py",
            "old_str": "            results = [p for p in results if p.rating <= min_rating]  # BUG: inverted comparison",
            "new_str": "            results = [p for p in results if p.rating >= min_rating]",
        })),
        _tool_use_response(("c6", "patch", {
            "path": "src/repo.py",
            "old_str": "    def update(self, product: Product) -> bool:\n        return product.id in self._products  # BUG: reports existence but never updates",
            "new_str": "    def update(self, product: Product) -> bool:\n        if product.id in self._products:\n            self._products[product.id] = product\n            return True\n        return False",
        })),
        _tool_use_response(("c7", "patch", {
            "path": "src/repo.py",
            "old_str": "    def delete(self, product_id: str) -> bool:\n        return product_id in self._products  # BUG: reports existence but never deletes",
            "new_str": "    def delete(self, product_id: str) -> bool:\n        return self._products.pop(product_id, None) is not None",
        })),
        _tool_use_response(("c8", "bash", {"command": "python -m pytest tests/test_catalog.py -v"})),
        _text_response("All 5 bugs fixed: pass→continue, paginate offset, min_rating comparison, update(), delete()."),
    ])
    config = AgentConfig(max_turns=12)

    stats_content_after: str | None = None
    repo_content_after: str | None = None
    bash_output: str | None = None

    with tempfile.TemporaryDirectory() as tmpdir:
        for item in TARGET_BUG_DIR.iterdir():
            src = item
            dst = Path(tmpdir) / item.name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        agent = Agent(config, backend)
        traj = agent.run("Fix all bugs in stats.py and repo.py", tmpdir)
        stats_content_after = (Path(tmpdir) / "src" / "stats.py").read_text(encoding="utf-8")
        repo_content_after = (Path(tmpdir) / "src" / "repo.py").read_text(encoding="utf-8")
        tool_result_events = [e for e in traj.events if e.type == "tool_result"]
        if len(tool_result_events) >= 8:
            bash_output = tool_result_events[7].payload.get("content", "")

    # 验证轨迹完整性
    types = [e.type for e in traj.events]
    assert "run_start" in types
    assert "run_end" in types
    assert traj.events[-1].payload["reason"] == "end_turn"
    assert backend.call_count == 9

    tool_call_events = [e for e in traj.events if e.type == "tool_call"]
    assert len(tool_call_events) == 8

    tool_names = [e.payload.get("name") for e in tool_call_events]
    assert tool_names == ["read_file", "read_file", "patch", "patch", "patch", "patch", "patch", "bash"]

    # V1: stats.py pass → continue
    assert stats_content_after is not None
    for i, line in enumerate(stats_content_after.splitlines()):
        if "p.stock == 0" in line:
            assert "continue" in stats_content_after.splitlines()[i + 1]
            break

    # V2: repo.py paginate
    assert repo_content_after is not None
    assert "(page - 1) * page_size" in repo_content_after or "(page-1) * page_size" in repo_content_after

    # V3: repo.py min_rating
    assert ">= min_rating" in repo_content_after

    # H1: repo.py update() 有赋值逻辑
    assert "self._products[product.id] = product" in repo_content_after

    # H2: repo.py delete() 有 pop
    assert ".pop(" in repo_content_after

    # 验证 pytest 全部通过
    assert bash_output is not None
    assert "8 passed" in bash_output, f"expected 8 passed, got: {bash_output[:200]}"


# ── Trajectory resume workdir ─────────────────────────────────────────────────


def test_to_messages_workdir_prefix_matches_fresh_start():
    """to_messages() 重建的首条消息必须与 Agent.start() 注入的完全一致。"""
    db_path = "runs/test_workdir.db"
    config = AgentConfig(max_turns=1, db_path=db_path)
    backend = FakeBackend([_text_response("ok")])

    with tempfile.TemporaryDirectory() as tmpdir:
        agent = Agent(config, backend)
        traj = agent.run("do the thing", tmpdir)

        # 抓取 run_start 事件里的 task + workdir
        rs = next(e for e in traj.events if e.type == EventType.run_start)
        expected_task = rs.payload.get("task", "")

        # to_messages() 重建
        msgs = traj.to_messages()
        assert len(msgs) >= 2
        assert msgs[0].role == "system"
        first_user_text = "".join(
            b.text for b in msgs[1].content
        )

    # 用户消息应与 task 原文一致（不含 workdir 前缀）
    assert first_user_text == expected_task, (
        f"to_messages() user message mismatch:\n"
        f"  expected: {expected_task!r}\n"
        f"  got:      {first_user_text!r}"
    )

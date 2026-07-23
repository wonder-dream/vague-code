"""CLI integration tests — L1 (args + config), L2 (config passing), L3 (mock pipeline)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from src.agent.config import AgentConfig
from src.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakeBackend:
    """Minimal fake backend for CLI pipeline tests (non-stream only)."""

    def __init__(self, responses: list[ModelResponse | Exception]):
        self.responses = responses
        self.call_count = 0

    def complete(
        self, messages: list, tools: list | None = None, config: dict | None = None
    ) -> ModelResponse:
        r = self.responses[self.call_count]
        self.call_count += 1
        if isinstance(r, Exception):
            raise r
        return r


def _text_response(text: str = "ok", stop_reason: StopReason = StopReason.end_turn) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=stop_reason,
        usage=NormalizedUsage(input_tokens=5, output_tokens=3),
    )


def _rate_limit_error():
    import httpx
    from openai import RateLimitError
    req = httpx.Request("POST", "https://api.example.com")
    resp = httpx.Response(429, request=req)
    return RateLimitError("rate limited", response=resp, body=None)


# ── L1: Argument parsing + config validation ────────────────────────────────


class TestCliArgumentParsing:
    """Tests that exercise main(argv) for config validation errors."""

    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("src.cli._resolve_api_key", lambda: "sk-fake")
        monkeypatch.setattr("src.cli.sys.exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    def test_no_args(self):
        """xcode with no arguments — task is required unless --resume."""
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main([])
        assert exc.value.code == 2  # parser.error exits with code 2

    def test_no_resume_and_no_task(self, monkeypatch):
        """--resume not given, no task positional — parser.error fires."""
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["--db-path", str(Path.cwd() / "runs" / "runs.db")])
        assert exc.value.code == 2  # parser.error exits with code 2

    def test_invalid_max_turns_zero(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--max-turns", "0"])
        assert exc.value.code == 1

    def test_invalid_max_turns_negative(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--max-turns", "-1"])
        assert exc.value.code == 1

    def test_invalid_timeout_zero(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--timeout-s", "0"])
        assert exc.value.code == 1

    def test_invalid_db_extension(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--db-path", str(tmp_path / "test.txt")])
        assert exc.value.code == 1

    def test_invalid_model_with_spaces(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--model", "bad model"])
        assert exc.value.code == 1

    def test_invalid_retry_max_attempts_negative(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--retry-max-attempts", "-1"])
        assert exc.value.code == 1

    def test_invalid_retry_base_s_zero(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--retry-base-s", "0"])
        assert exc.value.code == 1

    def test_invalid_retry_max_delay_s_zero(self):
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task", "--retry-max-delay-s", "0"])
        assert exc.value.code == 1

    def test_no_api_key(self, monkeypatch):
        monkeypatch.setattr("src.cli._resolve_api_key", lambda: None)
        from src.cli import _resolve_api_key
        assert _resolve_api_key() is None
        # The main() should exit 1 with key message
        with pytest.raises(SystemExit) as exc:
            from src.cli import main
            main(["task"])
        assert exc.value.code == 1


# ── L2: Config passing ───────────────────────────────────────────────────────


class TestCliConfigPassing:
    """Verify CLI flags correctly propagate to AgentConfig and backend."""

    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("src.cli._resolve_api_key", lambda: "sk-fake")
        monkeypatch.setattr("src.cli.sys.exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    def test_config_defaults_propagate(self, monkeypatch):
        captured: dict = {}

        def fake_backend(api_key, base_url, timeout_s):
            captured["timeout_s"] = timeout_s
            return _FakeBackend([_text_response("hi")])
        monkeypatch.setattr("src.cli.create_deepseek_backend", fake_backend)
        monkeypatch.setattr("src.cli.Console", lambda: None)
        monkeypatch.setattr("src.cli.RichStreamVisitor", lambda *a, **kw: type("V", (), {})())
        monkeypatch.setattr("src.cli.dispatch_event", lambda ev, v: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        main(["hello", "."])

        assert captured.get("timeout_s") == 120.0

    def test_timeout_flag_propagates(self, monkeypatch):
        captured: dict = {}

        def fake_backend(api_key, base_url, timeout_s):
            captured["timeout_s"] = timeout_s
            return _FakeBackend([_text_response("hi")])
        monkeypatch.setattr("src.cli.create_deepseek_backend", fake_backend)
        monkeypatch.setattr("src.cli.Console", lambda: None)
        monkeypatch.setattr("src.cli.RichStreamVisitor", lambda *a, **kw: type("V", (), {})())
        monkeypatch.setattr("src.cli.dispatch_event", lambda ev, v: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        main(["hello", ".", "--timeout-s", "30"])

        assert captured.get("timeout_s") == 30.0

    def test_no_retry_flag_propagates(self, monkeypatch):
        captured: dict = {}

        def capture_backend(api_key, base_url, timeout_s):
            return _FakeBackend([_text_response("hi")])

        monkeypatch.setattr("src.cli.create_deepseek_backend", capture_backend)
        monkeypatch.setattr("src.cli.Console", lambda: None)
        monkeypatch.setattr("src.cli.RichStreamVisitor", lambda *a, **kw: type("V", (), {})())
        monkeypatch.setattr("src.cli.dispatch_event", lambda ev, v: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        from src.agent.config import AgentConfig as AC
        original_init = AC.__init__
        def tracked_init(self, *a, **kw):
            captured["config"] = kw
            return original_init(self, *a, **kw)
        monkeypatch.setattr(AC, "__init__", tracked_init)

        main(["hello", ".", "--no-retry"])
        transport = captured["config"]["transport"]
        assert not transport["retry_enabled"] if isinstance(transport, dict) else not transport.retry_enabled

    def test_verbose_flag_propagates(self, monkeypatch):
        captured: dict = {}

        def fake_backend(api_key, base_url, timeout_s):
            return _FakeBackend([_text_response("hi")])
        monkeypatch.setattr("src.cli.create_deepseek_backend", fake_backend)
        monkeypatch.setattr("src.cli.Console", lambda: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        class TrackingVisitor:
            def __init__(self, *a, **kw):
                captured["verbose"] = kw.get("verbose", False)
        monkeypatch.setattr("src.cli.RichStreamVisitor", TrackingVisitor)
        monkeypatch.setattr("src.cli.dispatch_event", lambda ev, v: None)

        from src.cli import main
        main(["hello", ".", "--verbose"])
        assert captured.get("verbose") is True


# ── L3: Mock pipeline — full main() with fake backend ───────────────────────


class TestCliMockPipeline:
    """Run main() with a monkeypatched backend and verify trajectory/output."""

    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("src.cli._resolve_api_key", lambda: "sk-fake")
        monkeypatch.setattr("time.sleep", lambda _: None)
        monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)

    def test_text_task_succeeds(self, monkeypatch, capsys):
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from src.cli import main
        main(["say hi", "."])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err

    def test_export_jsonl_to_directory_errors(self, monkeypatch, capsys, tmp_path):
        """--export-jsonl pointing to an existing directory must give clear error."""
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("ok")]))

        from src.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["hi", ".", "--export-jsonl", str(tmp_path)])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "is a directory" in err

    def test_export_jsonl(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))
        jsonl_path = tmp_path / "out.jsonl"
        from src.cli import main
        main(["hi", ".", "--export-jsonl", str(jsonl_path)])

        assert jsonl_path.exists()
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 3

        err = capsys.readouterr().err
        assert "Trajectory exported" in err

    def test_no_stream(self, monkeypatch, capsys):
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from src.cli import main
        main(["hi", ".", "--no-stream"])

        result = capsys.readouterr()
        # Should complete successfully without streaming output
        assert "finished, reason: end_turn" in result.err

    def test_verbose_output(self, monkeypatch, capsys):
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from src.cli import main
        main(["hi", ".", "--verbose"])

        out = capsys.readouterr().out
        assert "model: deepseek-v4-flash" in out or "model" in out

    def test_retry_success(self, monkeypatch, capsys):
        backend = _FakeBackend([_rate_limit_error(), _text_response("ok")])
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        main(["hi", "."])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err
        assert backend.call_count == 2

    def test_retry_notice_appears_in_output(self, monkeypatch, capsys, tmp_path):
        """L3-8: RetryNotice should appear as live text in CLI output."""
        backend = _FakeBackend([_rate_limit_error(), _text_response("ok")])
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        main(["hi", "."])

        out = capsys.readouterr().out
        assert "⚠ 请求失败" in out or "retry" in out.lower()

    def test_retry_disabled_fails_immediately(self, monkeypatch, capsys):
        backend = _FakeBackend([_rate_limit_error()])
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        main(["hi", ".", "--no-retry"])
        result = capsys.readouterr()
        # Agent completes with error, no SystemExit
        assert "finished, reason: llm_error" in result.err

    def test_non_retryable_error_no_retry(self, monkeypatch, capsys):
        import httpx
        from openai import BadRequestError
        req = httpx.Request("POST", "https://api.example.com")
        resp = httpx.Response(400, request=req)
        custom_err = BadRequestError("bad request", response=resp, body=None)
        backend = _FakeBackend([custom_err])
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: backend)

        from src.cli import main
        main(["hi", "."])
        result = capsys.readouterr()
        # Non-retryable: Agent completes with error, no SystemExit
        assert "finished, reason: llm_error" in result.err

    def test_retry_exhausted_shows_error(self, monkeypatch, capsys):
        errors = [_rate_limit_error() for _ in range(6)]  # 1 initial + 5 retries = 6
        backend = _FakeBackend(errors)
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from src.cli import main
        main(["hi", "."])
        result = capsys.readouterr()
        # Exhausted: completes with error, shows retry notices on stdout
        assert "finished, reason: llm_error" in result.err
        assert "请求失败" in result.out


# ── Resume CLI tests ─────────────────────────────────────────────────────────


class TestCliResume:
    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("src.cli._resolve_api_key", lambda: "sk-fake")
        monkeypatch.setattr("time.sleep", lambda _: None)

    def _create_checkpoint_db(self, tmp_path: Path) -> tuple[str, str]:
        """Create and return (run_id, db_path) with a checkpoint state."""
        from src.agent.trajectory import Trajectory, EventType
        db_path = str(tmp_path / "checkpoint.db")
        config = AgentConfig(max_turns=5, db_path=db_path)
        traj = Trajectory(run_id="cli_resume_test", config=config)
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
        (tmp_path / "x.txt").write_text("content", encoding="utf-8")
        return "cli_resume_test", db_path

    def test_resume_pending_tools(self, monkeypatch, capsys, tmp_path):
        run_id, db_path = self._create_checkpoint_db(tmp_path)
        monkeypatch.setattr("src.cli.create_deepseek_backend",
                            lambda *a, **kw: _FakeBackend([_text_response("done")]))

        from src.cli import main
        main(["--resume", run_id, "--db-path", db_path])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err

    def test_resume_with_export_jsonl(self, monkeypatch, capsys, tmp_path):
        run_id, db_path = self._create_checkpoint_db(tmp_path)
        monkeypatch.setattr("src.cli.create_deepseek_backend",
                            lambda *a, **kw: _FakeBackend([_text_response("done")]))
        jsonl_path = tmp_path / "resume.jsonl"

        from src.cli import main
        main(["--resume", run_id, "--db-path", db_path, "--export-jsonl", str(jsonl_path)])

        assert jsonl_path.exists()
        err = capsys.readouterr().err
        assert "Trajectory exported" in err

    def test_resume_finished_run(self, monkeypatch, capsys, tmp_path):
        db_path = str(tmp_path / "t.db")
        config = AgentConfig(max_turns=5, db_path=db_path)
        # Create a completed run
        from src.agent.loop import Agent
        agent = Agent(config, _FakeBackend([_text_response("ok")]))
        traj = agent.run("x", ".")
        run_id = traj.run_id

        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([]))

        from src.cli import main
        main(["--resume", run_id, "--db-path", db_path])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err

    def test_resume_nonexistent_run(self, monkeypatch, capsys, tmp_path):
        db_path = str(tmp_path / "t.db")
        monkeypatch.setattr("src.cli.create_deepseek_backend", lambda *a, **kw: _FakeBackend([]))

        from src.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["--resume", "no_such_run", "--db-path", db_path])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Fatal error" in err

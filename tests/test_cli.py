"""CLI integration tests — L1 (args + config), L2 (config passing), L3 (mock pipeline)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from vague_code.agent.config import AgentConfig
from vague_code.agent.ir import (
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
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: "sk-fake")
        monkeypatch.setattr("vague_code.cli.sys.exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    def test_no_args(self):
        """vague-code with no arguments — task is required unless --resume."""
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main([])
        assert exc.value.code == 2  # parser.error exits with code 2

    def test_no_resume_and_no_task(self, monkeypatch):
        """--resume not given, no task positional — parser.error fires."""
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["--db-path", str(Path.cwd() / "runs" / "runs.db")])
        assert exc.value.code == 2  # parser.error exits with code 2

    def test_invalid_max_turns_zero(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--max-turns", "0"])
        assert exc.value.code == 1

    def test_invalid_max_turns_negative(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--max-turns", "-1"])
        assert exc.value.code == 1

    def test_invalid_timeout_zero(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--timeout-s", "0"])
        assert exc.value.code == 1

    def test_invalid_db_extension(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--db-path", str(tmp_path / "test.txt")])
        assert exc.value.code == 1

    def test_invalid_model_with_spaces(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--model", "bad model"])
        assert exc.value.code == 1

    def test_invalid_retry_max_attempts_negative(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--retry-max-attempts", "-1"])
        assert exc.value.code == 1

    def test_invalid_retry_base_s_zero(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--retry-base-s", "0"])
        assert exc.value.code == 1

    def test_invalid_retry_max_delay_s_zero(self):
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task", "--retry-max-delay-s", "0"])
        assert exc.value.code == 1

    def test_no_api_key(self, monkeypatch):
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: None)
        from vague_code.cli import _resolve_api_key
        assert _resolve_api_key("DEEPSEEK_API_KEY") is None
        # The main() should exit 1 with key message
        with pytest.raises(SystemExit) as exc:
            from vague_code.cli import main
            main(["task"])
        assert exc.value.code == 1


# ── L2: Config passing ───────────────────────────────────────────────────────


class TestCliConfigPassing:
    """Verify CLI flags correctly propagate to AgentConfig and backend."""

    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: "sk-fake")
        monkeypatch.setattr("vague_code.cli.sys.exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))

    def test_config_defaults_propagate(self, monkeypatch):
        captured: dict = {}

        def fake_backend(api_key, base_url, timeout_s):
            captured["timeout_s"] = timeout_s
            return _FakeBackend([_text_response("hi")])
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", fake_backend)
        monkeypatch.setattr("vague_code.cli.Console", lambda: None)
        monkeypatch.setattr("vague_code.cli.RichStreamVisitor", lambda *a, **kw: type("V", (), {})())
        monkeypatch.setattr("vague_code.cli.dispatch_event", lambda ev, v: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        main(["hello", "."])

        assert captured.get("timeout_s") == 120.0

    def test_timeout_flag_propagates(self, monkeypatch):
        captured: dict = {}

        def fake_backend(api_key, base_url, timeout_s):
            captured["timeout_s"] = timeout_s
            return _FakeBackend([_text_response("hi")])
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", fake_backend)
        monkeypatch.setattr("vague_code.cli.Console", lambda: None)
        monkeypatch.setattr("vague_code.cli.RichStreamVisitor", lambda *a, **kw: type("V", (), {})())
        monkeypatch.setattr("vague_code.cli.dispatch_event", lambda ev, v: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        main(["hello", ".", "--timeout-s", "30"])

        assert captured.get("timeout_s") == 30.0

    def test_no_retry_flag_propagates(self, monkeypatch):
        captured: dict = {}

        def capture_backend(api_key, base_url, timeout_s):
            return _FakeBackend([_text_response("hi")])

        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", capture_backend)
        monkeypatch.setattr("vague_code.cli.Console", lambda: None)
        monkeypatch.setattr("vague_code.cli.RichStreamVisitor", lambda *a, **kw: type("V", (), {})())
        monkeypatch.setattr("vague_code.cli.dispatch_event", lambda ev, v: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        from vague_code.agent.config import AgentConfig as AC
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
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", fake_backend)
        monkeypatch.setattr("vague_code.cli.Console", lambda: None)
        monkeypatch.setattr("time.sleep", lambda _: None)

        class TrackingVisitor:
            def __init__(self, *a, **kw):
                captured["verbose"] = kw.get("verbose", False)
        monkeypatch.setattr("vague_code.cli.RichStreamVisitor", TrackingVisitor)
        monkeypatch.setattr("vague_code.cli.dispatch_event", lambda ev, v: None)

        from vague_code.cli import main
        main(["hello", ".", "--verbose"])
        assert captured.get("verbose") is True


# ── L3: Mock pipeline — full main() with fake backend ───────────────────────


class TestCliMockPipeline:
    """Run main() with a monkeypatched backend and verify trajectory/output."""

    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: "sk-fake")
        monkeypatch.setattr("time.sleep", lambda _: None)
        monkeypatch.setattr(random, "uniform", lambda lo, hi: 0.0)

    def test_text_task_succeeds(self, monkeypatch, capsys):
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from vague_code.cli import main
        main(["say hi", ".", "--verbose"])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err

    def test_export_jsonl_to_directory_errors(self, monkeypatch, capsys, tmp_path):
        """--export-jsonl pointing to an existing directory must give clear error."""
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("ok")]))

        from vague_code.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["hi", ".", "--export-jsonl", str(tmp_path)])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "is a directory" in err

    def test_export_jsonl(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))
        jsonl_path = tmp_path / "out.jsonl"
        from vague_code.cli import main
        main(["hi", ".", "--export-jsonl", str(jsonl_path)])

        assert jsonl_path.exists()
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 3

        err = capsys.readouterr().err
        assert "Trajectory exported" in err

    def test_no_stream(self, monkeypatch, capsys):
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from vague_code.cli import main
        main(["hi", ".", "--no-stream", "--verbose"])

        result = capsys.readouterr()
        # Should complete successfully without streaming output
        assert "finished, reason: end_turn" in result.err

    def test_normal_output_no_metadata(self, monkeypatch, capsys):
        """Without --verbose, the `Run X finished` line should NOT appear."""
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from vague_code.cli import main
        main(["hi", "."])

        result = capsys.readouterr()
        assert "finished, reason:" not in result.err
        assert "finished, reason:" not in result.out

    def test_verbose_output(self, monkeypatch, capsys):
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from vague_code.cli import main
        main(["hi", ".", "--verbose"])

        out = capsys.readouterr().out
        assert "model: deepseek-v4-flash" in out or "model" in out

    def test_retry_success(self, monkeypatch, capsys):
        backend = _FakeBackend([_rate_limit_error(), _text_response("ok")])
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        main(["hi", ".", "--verbose"])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err
        assert backend.call_count == 2

    def test_retry_notice_appears_in_output(self, monkeypatch, capsys, tmp_path):
        """L3-8: RetryNotice should appear as live text in CLI output."""
        backend = _FakeBackend([_rate_limit_error(), _text_response("ok")])
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        main(["hi", "."])

        out = capsys.readouterr().out
        assert "⚠ 请求失败" in out or "retry" in out.lower()

    def test_retry_disabled_fails_immediately(self, monkeypatch, capsys):
        backend = _FakeBackend([_rate_limit_error()])
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        main(["hi", ".", "--no-retry", "--verbose"])
        result = capsys.readouterr()
        # Agent completes with error, no SystemExit
        assert "finished, reason: rate_limit" in result.err

    def test_non_retryable_error_no_retry(self, monkeypatch, capsys):
        import httpx
        from openai import BadRequestError
        req = httpx.Request("POST", "https://api.example.com")
        resp = httpx.Response(400, request=req)
        custom_err = BadRequestError("bad request", response=resp, body=None)
        backend = _FakeBackend([custom_err])
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: backend)

        from vague_code.cli import main
        main(["hi", ".", "--verbose"])
        result = capsys.readouterr()
        # Non-retryable: Agent completes with error, no SystemExit
        assert "finished, reason: llm_error" in result.err

    def test_retry_exhausted_shows_error(self, monkeypatch, capsys):
        errors = [_rate_limit_error() for _ in range(6)]  # 1 initial + 5 retries = 6
        backend = _FakeBackend(errors)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: backend)
        monkeypatch.setattr("time.sleep", lambda _: None)

        from vague_code.cli import main
        main(["hi", ".", "--verbose"])
        result = capsys.readouterr()
        # Exhausted: completes with error, shows retry notices on stdout
        assert "finished, reason: rate_limit" in result.err
        assert "retry:" in result.out


# ── Resume CLI tests ─────────────────────────────────────────────────────────


class TestCliResume:
    @pytest.fixture(autouse=True)
    def _fix_env(self, monkeypatch):
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: "sk-fake")
        monkeypatch.setattr("vague_code.cli.sys.exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))



    def _create_checkpoint_db(self, tmp_path: Path) -> tuple[str, str]:
        """Create and return (run_id, db_path) with a checkpoint state."""
        from vague_code.agent.trajectory import Trajectory, EventType
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
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend",
                            lambda *a, **kw: _FakeBackend([_text_response("done")]))

        from vague_code.cli import main
        main(["--resume", run_id, "--db-path", db_path, "--verbose"])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err

    def test_resume_with_export_jsonl(self, monkeypatch, capsys, tmp_path):
        run_id, db_path = self._create_checkpoint_db(tmp_path)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend",
                            lambda *a, **kw: _FakeBackend([_text_response("done")]))
        jsonl_path = tmp_path / "resume.jsonl"

        from vague_code.cli import main
        main(["--resume", run_id, "--db-path", db_path, "--export-jsonl", str(jsonl_path), "--verbose"])

        assert jsonl_path.exists()
        err = capsys.readouterr().err
        assert "Trajectory exported" in err

    def test_resume_finished_run(self, monkeypatch, capsys, tmp_path):
        db_path = str(tmp_path / "t.db")
        config = AgentConfig(max_turns=5, db_path=db_path)
        from vague_code.agent.loop import Agent
        agent = Agent(config, _FakeBackend([_text_response("ok")]))
        traj = agent.run("x", ".")
        run_id = traj.run_id

        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([]))

        from vague_code.cli import main
        main(["--resume", run_id, "--db-path", db_path, "--verbose"])

        err = capsys.readouterr().err
        assert "finished, reason: end_turn" in err

    def test_resume_nonexistent_run(self, monkeypatch, capsys, tmp_path):
        db_path = str(tmp_path / "t.db")
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", lambda *a, **kw: _FakeBackend([]))

        from vague_code.cli import main
        with pytest.raises(SystemExit) as exc:
            main(["--resume", "no_such_run", "--db-path", db_path])
        assert exc.value.code == 1

    def test_mode_flag_sets_permission_mode(self, monkeypatch, capsys):
        """S1: --mode auto 透传到 AgentConfig.permission_mode（CLI 可无人值守编辑）。"""
        orig = AgentConfig

        class _CapturedConfig(orig):
            instances: list = []

            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                _CapturedConfig.instances.append(self)

        monkeypatch.setattr("vague_code.cli.AgentConfig", _CapturedConfig)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend",
                            lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from vague_code.cli import main
        main(["hi", ".", "--mode", "auto"])
        assert _CapturedConfig.instances
        assert _CapturedConfig.instances[-1].permission_mode == "auto"

    def test_permission_rules_loaded_from_workdir(self, monkeypatch, capsys, tmp_path):
        """S1: CLI 加载工作区 .agent/permission-rules.json 并注入 agent。"""
        (tmp_path / ".agent").mkdir()
        (tmp_path / ".agent" / "permission-rules.json").write_text(
            json.dumps([{"pattern": "write_file .*", "action": "deny"}]), encoding="utf-8"
        )
        seen: list = []
        monkeypatch.setattr(
            "vague_code.agent.loop.Agent.add_permission_rule",
            lambda self, pattern, action="allow": seen.append((pattern, action)),
        )
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend",
                            lambda *a, **kw: _FakeBackend([_text_response("hello")]))

        from vague_code.cli import main
        main(["hi", str(tmp_path), "--no-repo-map"])
        assert ("write_file .*", "deny") in seen

    def test_provider_openai_uses_openai_defaults(self, monkeypatch, capsys):
        """--provider openai → base_url=api.openai.com/v1、key env=OPENAI_API_KEY。"""
        captured: dict = {}

        def _capture_backend(api_key, base_url, timeout_s):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            return _FakeBackend([_text_response("hello")])

        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", _capture_backend)

        from vague_code.cli import main
        main(["hi", ".", "--provider", "openai"])
        assert captured.get("base_url") == "https://api.openai.com/v1"
        assert captured.get("api_key") == "sk-OPENAI_API_KEY"

    def test_base_url_and_api_key_env_override(self, monkeypatch, capsys):
        """--base-url/--api-key-env 覆盖 provider 默认（任意 OpenAI 兼容端点）。"""
        captured: dict = {}

        def _capture_backend(api_key, base_url, timeout_s):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            return _FakeBackend([_text_response("hello")])

        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", _capture_backend)

        from vague_code.cli import main
        main(["hi", ".", "--base-url", "https://openrouter.ai/api/v1",
              "--api-key-env", "OPENROUTER_API_KEY", "--model", "openai/gpt-4o"])
        assert captured.get("base_url") == "https://openrouter.ai/api/v1"
        assert captured.get("api_key") == "sk-OPENROUTER_API_KEY"

    def test_provider_defaults_stable_for_deepseek(self, monkeypatch, capsys):
        """默认 provider=deepseek 行为不变（base_url/key env 与旧版一致）。"""
        captured: dict = {}

        def _capture_backend(api_key, base_url, timeout_s):
            captured["base_url"] = base_url
            return _FakeBackend([_text_response("hello")])

        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", _capture_backend)

        from vague_code.cli import main
        main(["hi", "."])
        assert captured.get("base_url") == "https://api.deepseek.com"

    def test_config_file_provider_resolved(self, monkeypatch, capsys, tmp_path):
        """vague-code.json 里的自定义 provider：--provider fox 生效（baseUrl/apiKeyEnv）。"""
        (tmp_path / "vague-code.json").write_text(
            json.dumps({
                "defaultProvider": "fox",
                "defaultModel": "gpt-5.6-sol",
                "providers": {
                    "fox": {
                        "baseUrl": "https://code.newcli.com/codex/v1",
                        "apiKeyEnv": "RELAY_KEY",
                    }
                },
            }),
            encoding="utf-8",
        )
        captured: dict = {}

        def _capture_backend(api_key, base_url, timeout_s):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            return _FakeBackend([_text_response("hello")])

        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", _capture_backend)

        from vague_code.cli import main
        main(["hi", str(tmp_path), "--provider", "fox", "--no-repo-map"])
        assert captured.get("base_url") == "https://code.newcli.com/codex/v1"
        assert captured.get("api_key") == "sk-RELAY_KEY"

    def test_config_default_provider_and_model(self, monkeypatch, capsys, tmp_path):
        """配置文件 defaultProvider/defaultModel：零参数（无 --provider/--model）生效。"""
        (tmp_path / "vague-code.json").write_text(
            json.dumps({
                "defaultProvider": "fox",
                "defaultModel": "gpt-5.6-luna",
                "providers": {
                    "fox": {"baseUrl": "https://relay.example.com/v1", "apiKeyEnv": "RELAY_KEY"}
                },
            }),
            encoding="utf-8",
        )
        captured: dict = {}

        def _capture_backend(api_key, base_url, timeout_s):
            captured["base_url"] = base_url
            return _FakeBackend([_text_response("hello")])

        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr("vague_code.agent.backend.create_deepseek_backend", _capture_backend)
        monkeypatch.setattr("vague_code.cli.AgentConfig", AgentConfig)

        from vague_code.cli import main
        main(["hi", str(tmp_path), "--no-repo-map"])
        assert captured.get("base_url") == "https://relay.example.com/v1"

    def test_init_command_generates_template(self, monkeypatch, capsys, tmp_path):
        """`vague-code init` 生成配置模板。"""
        from vague_code.cli import main
        out = str(tmp_path / "vague-code.json")
        main(["init", "--path", out])
        result = capsys.readouterr()
        assert "Created" in result.out
        assert json.loads(Path(out).read_text(encoding="utf-8"))["defaultProvider"] == "deepseek"

    def test_tui_missing_key_starts_setup(self, monkeypatch, capsys, tmp_path):
        """ADR-0037：缺 key 时 `tui` 不退出，以 needs_setup 启动引导。"""
        captured: dict = {}

        def _fake_tui_main(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: None)
        monkeypatch.setattr("vague_code.tui.main", _fake_tui_main)

        from vague_code.cli import main
        main(["tui", str(tmp_path)])
        assert captured.get("needs_setup") is True
        assert captured.get("backend") is None

    def test_responses_protocol_dispatches_responses_backend(self, monkeypatch, capsys, tmp_path):
        """protocol: responses 的 provider → ResponsesBackend（Codex 中转站）。"""
        (tmp_path / "vague-code.json").write_text(
            json.dumps({
                "defaultProvider": "fox",
                "providers": {
                    "fox": {
                        "baseUrl": "https://code.newcli.com/codex/v1",
                        "apiKeyEnv": "RELAY_KEY",
                        "protocol": "responses",
                    }
                },
            }),
            encoding="utf-8",
        )
        called: list = []
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr(
            "vague_code.agent.backend.create_responses_backend",
            lambda api_key, base_url, timeout_s: (called.append((api_key, base_url)), _FakeBackend([_text_response("hello")]))[1],
        )

        from vague_code.cli import main
        main(["hi", str(tmp_path), "--no-repo-map"])
        assert called and called[0][1] == "https://code.newcli.com/codex/v1"

    def test_anthropic_provider_user_agent_passthrough(self, monkeypatch, capsys, tmp_path):
        """providers.<name>.userAgent 配置 → create_anthropic_backend 收到 user_agent（中转站 UA 放行）。"""
        (tmp_path / "vague-code.json").write_text(
            json.dumps({
                "defaultProvider": "fox",
                "providers": {
                    "fox": {
                        "baseUrl": "https://code.newcli.com/claude",
                        "apiKeyEnv": "RELAY_KEY",
                        "protocol": "anthropic",
                        "userAgent": "claude-cli/1.0.66",
                    }
                },
            }),
            encoding="utf-8",
        )
        called: list = []
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr(
            "vague_code.agent.backend.create_anthropic_backend",
            lambda api_key, base_url, timeout_s, user_agent=None: (called.append((base_url, user_agent)), _FakeBackend([_text_response("hello")]))[1],
        )

        from vague_code.cli import main
        main(["hi", str(tmp_path), "--no-repo-map"])
        assert called and called[0] == ("https://code.newcli.com/claude", "claude-cli/1.0.66")

    def test_anthropic_provider_no_user_agent_defaults_none(self, monkeypatch, capsys, tmp_path):
        """未配置 userAgent → create_anthropic_backend 收到 None（SDK 默认 UA）。"""
        (tmp_path / "vague-code.json").write_text(
            json.dumps({
                "defaultProvider": "anthropic",
                "providers": {
                    "anthropic": {
                        "baseUrl": "https://api.anthropic.com",
                        "apiKeyEnv": "ANTHROPIC_API_KEY",
                        "protocol": "anthropic",
                    }
                },
            }),
            encoding="utf-8",
        )
        called: list = []
        monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda env: "sk-" + env)
        monkeypatch.setattr(
            "vague_code.agent.backend.create_anthropic_backend",
            lambda api_key, base_url, timeout_s, user_agent=None: (called.append(user_agent), _FakeBackend([_text_response("hello")]))[1],
        )

        from vague_code.cli import main
        main(["hi", str(tmp_path), "--no-repo-map"])
        assert called == [None]

    def test_anthropic_backend_sets_user_agent_header(self):
        """AnthropicBackend(user_agent=...) → SDK client default_headers 含自定义 UA（覆盖默认）。"""
        from vague_code.agent.backend import AnthropicBackend

        backend = AnthropicBackend(api_key="x", base_url="https://code.newcli.com/claude", user_agent="claude-cli/1.0.66")
        assert backend._client.default_headers.get("User-Agent") == "claude-cli/1.0.66"

        plain = AnthropicBackend(api_key="x")
        assert "claude-cli" not in plain._client.default_headers.get("User-Agent", "")

"""CLI subprocess tests — L4: real `vague-code` process invocation.

These tests run `uv run vague-code ...` as a subprocess, testing the full
entry point including argument parsing, env resolution, and error messages
exactly as the user sees them.

All tests in this file must be runnable WITHOUT a real API key."""

from __future__ import annotations

import os
import subprocess
import sys

VAGUE_CODE = [sys.executable, "-m", "vague_code.cli"]


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run vague-code CLI entry point with given args and return CompletedProcess.

    用 `python -m vague_code.cli` 而非 `uv run vague-code`：uv 在本环境（受限
    token）会偶发尝试重建项目而失败，python -m 直接走已安装包，等价覆盖
    argparse/参数解析/错误消息等真实 CLI 行为。
    """
    cmd = VAGUE_CODE + list(args)
    merged_env = {**os.environ, **env} if env is not None else dict(os.environ)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=merged_env)


class TestSubprocessArgs:
    """L4 tests that don't need any config/environment setup."""

    def test_help_output(self):
        """vague-code --help lists all flags."""
        result = _run("--help")
        assert result.returncode == 0
        assert "--resume RUN_ID" in result.stdout
        assert "--retry" in result.stdout
        assert "--no-retry" in result.stdout
        assert "--retry-max-attempts" in result.stdout
        assert "--retry-base-s" in result.stdout
        assert "--retry-max-delay-s" in result.stdout
        assert "--timeout-s" in result.stdout
        assert "--verbose" in result.stdout
        assert "--export-jsonl" in result.stdout
        assert "--max-turns" in result.stdout
        assert "--db-path" in result.stdout
        assert "--model" in result.stdout

    def test_no_args_errors(self):
        """vague-code with no args — parser.error about missing task."""
        result = _run()
        assert result.returncode == 2  # argparse parser.error
        # The project's .env may provide a key, so message may vary.
        # But without --resume and no task positional, parser.error fires
        # if API key is found. If no key, key error fires first.
        # Accept either message.
        messages = ["task is required", "DEEPSEEK_API_KEY", "usage:"]
        assert any(m in result.stderr or m in result.stdout for m in messages), (
            f"stderr: {result.stderr}, stdout: {result.stdout}"
        )

    def test_config_validation_errors(self):
        """Invalid flags produce Fatal error messages."""
        cases = [
            (["task", "--max-turns", "0"], ">= 1"),
            (["task", "--timeout-s", "0"], "> 0"),
            (["task", "--db-path", "nope.txt"], ".db"),
            (["task", "--model", "bad model"], "invalid"),
            (["task", "--retry-max-attempts", "-1"], ">= 0"),
            (["task", "--retry-base-s", "0"], "> 0"),
        ]
        for args, expected in cases:
            result = _run(*args, env={"DEEPSEEK_API_KEY": "sk-fake"})
            assert result.returncode != 0, f"Expected failure for {args}"
            assert expected in result.stderr, f"{args}: expected '{expected}' in stderr:\n{result.stderr}"

    def test_resume_non_existent_run(self, tmp_path):
        """--resume with run_id not in DB has sensible error."""
        db_path = tmp_path / "empty.db"
        db_path.write_text("", encoding="utf-8")
        result = _run("--resume", "nope", "--db-path", str(db_path),
                      env={"DEEPSEEK_API_KEY": "sk-fake"})
        assert result.returncode != 0
        assert "Fatal error" in result.stderr or "not found" in result.stderr

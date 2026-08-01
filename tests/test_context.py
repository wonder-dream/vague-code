from __future__ import annotations

from pathlib import Path

from src.agent.context import SystemPrompt


def test_identity_section_present() -> None:
    sp = SystemPrompt(Path.cwd())
    result = sp.build()
    assert "XClaw" in result


def test_session_includes_workdir() -> None:
    sp = SystemPrompt("/home/project")
    result = sp.build()
    assert "工作目录根路径:" in result
    assert "project" in result


def test_rules_appended_if_present(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "rules.md").write_text("always use type hints", encoding="utf-8")
    sp = SystemPrompt(tmp_path)
    result = sp.build()
    assert "always use type hints" in result

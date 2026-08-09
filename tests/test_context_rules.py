from __future__ import annotations

from pathlib import Path

from src.agent.context_rules import load_rules


def test_no_rules_returns_empty(tmp_path: Path) -> None:
    result = load_rules(str(tmp_path))
    assert result == ""


def test_single_rule_at_workdir(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "rules.md").write_text("do not use rm -rf", encoding="utf-8")
    result = load_rules(str(tmp_path))
    assert "do not use rm -rf" in result


def test_hierarchical_merging(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".agent").mkdir()
    (parent / ".agent" / "rules.md").write_text("rule from parent", encoding="utf-8")
    child = parent / "child"
    child.mkdir()
    (child / ".agent").mkdir()
    (child / ".agent" / "rules.md").write_text("rule from child", encoding="utf-8")
    result = load_rules(str(child))
    assert result.index("rule from parent") < result.index("rule from child")


def test_workdir_rules_override_parent(tmp_path: Path) -> None:
    parent = tmp_path / "base"
    parent.mkdir()
    (parent / ".agent").mkdir()
    (parent / ".agent" / "rules.md").write_text("base: always read first", encoding="utf-8")
    child = parent / "override"
    child.mkdir()
    (child / ".agent").mkdir()
    (child / ".agent" / "rules.md").write_text("override: use python -m pytest", encoding="utf-8")
    result = load_rules(str(child))
    assert "base: always read first" in result
    assert "override: use python -m pytest" in result


def test_load_rules_skips_binary_file(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    rules_file = agent_dir / "rules.md"
    rules_file.write_bytes(b'\x00\x01\xff\xfe\x80\x81')
    result = load_rules(str(tmp_path))
    assert result == ""
    assert rules_file.exists()


def test_load_rules_skips_too_large_file(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    rules_file = agent_dir / "rules.md"
    rules_file.write_text("x" * 20_000, encoding="utf-8")
    import src.agent.context_rules as cr
    orig = cr.MAX_RULES_SIZE
    try:
        cr.MAX_RULES_SIZE = 15_000
        result = load_rules(str(tmp_path))
        assert result == ""
    finally:
        cr.MAX_RULES_SIZE = orig

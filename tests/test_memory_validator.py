"""MemoryValidator 单测（ADR-0021 机制 1：写入前规则化事实校验）。

校验器是纯规则、无 LLM：对声明性事实（技术栈/路径）在 workdir 内做证据核对。
"""

from __future__ import annotations

from pathlib import Path

from vague_code.agent.memory_validator import MemoryValidator


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_db_stack_verified_when_evidence_exists(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\ndependencies = [\"sqlalchemy\"]\n")
    _write(tmp_path / "src" / "db.py", "import sqlite3\n")
    v = MemoryValidator(str(tmp_path))
    result = v.check("项目用 SQLite 做存储")
    assert result.level == "verified"
    assert result.evidence


def test_db_stack_contradicted_when_other_stack_evidenced(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\ndependencies = [\"sqlite-utils\"]\n")
    _write(tmp_path / "src" / "db.py", "import sqlite3\n")
    v = MemoryValidator(str(tmp_path))
    result = v.check("项目用 MySQL 做存储")
    assert result.level == "contradicted"
    assert result.evidence


def test_db_stack_unverified_when_no_evidence(tmp_path: Path) -> None:
    # workdir 空，无任何依赖/import 证据
    v = MemoryValidator(str(tmp_path))
    result = v.check("项目用 Redis 做缓存")
    assert result.level == "unverified"


def test_path_verified_when_file_exists(tmp_path: Path) -> None:
    _write(tmp_path / "config.yaml", "key: value\n")
    v = MemoryValidator(str(tmp_path))
    result = v.check("配置文件路径：config.yaml")
    assert result.level == "verified"
    assert result.evidence


def test_path_unverified_when_file_missing(tmp_path: Path) -> None:
    v = MemoryValidator(str(tmp_path))
    result = v.check("配置文件路径：missing.yaml")
    assert result.level == "unverified"


def test_no_rule_matched_is_verified(tmp_path: Path) -> None:
    v = MemoryValidator(str(tmp_path))
    result = v.check("构建命令用 uv run pytest 跑测试")
    assert result.level == "verified"


def test_empty_workdir_does_not_crash(tmp_path: Path) -> None:
    v = MemoryValidator(str(tmp_path / "nope"))
    result = v.check("项目用 SQLite 做存储")
    assert result.level in ("unverified", "verified")

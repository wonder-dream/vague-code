"""MemoryFile 单测（ADR-0014 记忆 v2：文件式记忆 .agent/memory.md）。"""

from __future__ import annotations

from pathlib import Path

from vague_code.agent.memory_file import MAX_BYTES, MAX_LINES, MemoryFile


def test_append_creates_file_with_header_and_section(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / ".agent" / "memory.md")
    ok = mf.append(title="构建命令", content="用 uv run pytest 跑测试", source_session="run123")
    assert ok is True
    text = mf.read()
    assert text.startswith("<!-- vague-code memory")
    assert "## 构建命令" in text
    assert "source: run123;" in text
    assert "uv run pytest 跑测试" in text


def test_append_dedup_by_content_hash(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    assert mf.append("t", "相同内容", "r1") is True
    assert mf.append("t2", "相同内容", "r2") is False
    assert mf.read().count("相同内容") == 1


def test_append_blank_content_ignored(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    assert mf.append("t", "   ", "r1") is False
    assert mf.read() == ""


def test_inject_text_empty_when_no_file(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    assert mf.inject_text() == ""


def test_inject_text_truncates_lines(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    content = "\n".join(f"行 {i}" for i in range(MAX_LINES + 50))
    mf.append("大文件", content, "r1")
    text = mf.inject_text()
    assert len(text.splitlines()) <= MAX_LINES


def test_inject_text_truncates_bytes_utf8_safe(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    content = "汉" * (MAX_BYTES + 1024)
    mf.append("大内容", content, "r1")
    text = mf.inject_text()
    assert len(text.encode("utf-8")) <= MAX_BYTES + 512  # 截断边界不超一字符
    assert "\ufffd" not in text  # 无非法截断字符


def test_remove_sections_only_target_run(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    assert mf.append("a", "内容 A", "run_a") is True
    assert mf.append("b", "内容 B", "run_b") is True
    removed = mf.remove_sections("run_a")
    assert removed == 1
    text = mf.read()
    assert "内容 A" not in text
    assert "内容 B" in text
    assert "## b" in text


def test_remove_sections_missing_run_returns_zero(tmp_path: Path) -> None:
    mf = MemoryFile(tmp_path / "memory.md")
    mf.append("a", "内容 A", "run_a")
    assert mf.remove_sections("nope") == 0
    assert "内容 A" in mf.read()


def test_per_workdir_isolation(tmp_path: Path) -> None:
    a = MemoryFile(tmp_path / "proj-a" / ".agent" / "memory.md")
    b = MemoryFile(tmp_path / "proj-b" / ".agent" / "memory.md")
    a.append("t", "A 项目的秘密", "r1")
    b.append("t", "B 项目的秘密", "r2")
    assert "A 项目" in a.read()
    assert "A 项目" not in b.read()
    assert "B 项目" in b.read()
    assert "B 项目" not in a.read()

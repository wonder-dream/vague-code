"""统一截断单测（对齐 PI truncate.ts 的 2000 行 / 50KB 业界参数）。"""

from __future__ import annotations

from vague_code.agent.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_output


def test_short_text_untouched() -> None:
    tr = truncate_output("hello\nworld\n")
    assert tr.content == "hello\nworld"
    assert tr.truncated is False
    assert tr.truncated_by is None
    assert tr.total_lines == 2


def test_line_limit_truncates() -> None:
    text = "\n".join(f"line {i}" for i in range(DEFAULT_MAX_LINES + 100))
    tr = truncate_output(text)
    assert tr.truncated is True
    assert tr.truncated_by == "lines"
    assert tr.output_lines == DEFAULT_MAX_LINES
    assert len(tr.content.splitlines()) == DEFAULT_MAX_LINES


def test_byte_limit_truncates_no_partial_line() -> None:
    lines = [f"row {i} " + "x" * 100 for i in range(2000)]
    text = "\n".join(lines)
    tr = truncate_output(text, max_lines=10_000)
    assert tr.truncated is True
    assert tr.truncated_by == "bytes"
    assert len(tr.content.encode("utf-8")) <= DEFAULT_MAX_BYTES
    # 最后一行完整（不截半行）
    assert tr.content.endswith("x")


def test_utf8_safe_no_replacement_char() -> None:
    text = "中文内容\n" * 30_000
    tr = truncate_output(text, max_lines=100_000)
    assert tr.truncated is True
    assert "\ufffd" not in tr.content
    assert len(tr.content.encode("utf-8")) <= DEFAULT_MAX_BYTES


def test_custom_limits() -> None:
    tr = truncate_output("a\nb\nc\nd\n", max_lines=3, max_bytes=10_000)
    assert tr.truncated_by == "lines"
    assert tr.output_lines == 3


def test_empty_text() -> None:
    tr = truncate_output("")
    assert tr.content == ""
    assert tr.truncated is False
    assert tr.total_lines == 0

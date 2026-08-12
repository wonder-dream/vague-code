from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vague_code.agent.tools import DEFAULT_TOOLS, BashTool, GrepTool, _is_test_command, _summarize_test_output


# ── helpers ──────────────────────────────────────────────────────

def _ws(tmp_path: Path, name: str = "ws") -> Path:
    p = tmp_path / name
    p.mkdir()
    return p


# ══════════════════════════════════════════════════════════════════
# read_file
# ══════════════════════════════════════════════════════════════════

def test_read_file_happy_path(tmp_path):
    ws = _ws(tmp_path)
    (ws / "hello.txt").write_text("hello world", encoding="utf-8")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    result = handler({"path": "hello.txt"})
    assert result.output.endswith("hello world")


def test_read_file_not_found(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(FileNotFoundError):
        handler({"path": "nope.txt"})


def test_read_file_empty_path(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供 path"):
        handler({"path": ""})


def test_read_file_null_path(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": None})


def test_read_file_path_traversal_blocked(tmp_path):
    ws = _ws(tmp_path)
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    (ws2 / "secret.txt").write_text("TOP SECRET")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(PermissionError):
        handler({"path": "../ws2/secret.txt"})


def test_read_file_rejects_null_byte(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="空字节|null"):
        handler({"path": "foo\x00.txt"})


def test_read_file_truncates_large_file(tmp_path):
    ws = _ws(tmp_path)
    big = ws / "big.txt"
    big.write_text(("A" * 100 + "\n") * 600, encoding="utf-8")  # 60KB 多行，超 50KB 上限
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    result = handler({"path": "big.txt"})
    assert "输出截断于" in result.output
    assert len(result.output) < 60_000


def test_read_file_strips_utf8_bom(tmp_path):
    ws = _ws(tmp_path)
    (ws / "bom.txt").write_bytes(b'\xef\xbb\xbf{"key": "value"}')
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    result = handler({"path": "bom.txt"})
    assert '{"key"' in result.output
    assert "\ufeff" not in result.output


# ══════════════════════════════════════════════════════════════════
# write_file
# ══════════════════════════════════════════════════════════════════

def test_write_file_creates_file(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    result = handler({"path": "new.txt", "content": "hello"})
    assert "字符" in result.output
    assert (ws / "new.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_overwrite_false_rejects(tmp_path):
    """默认允许覆盖（P0-1：编辑源码的直接通道）；显式 overwrite=false 才拒绝。"""
    ws = _ws(tmp_path)
    (ws / "existing.txt").write_text("old", encoding="utf-8")
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(FileExistsError):
        handler({"path": "existing.txt", "content": "new", "overwrite": False})


def test_write_file_overwrites_by_default(tmp_path):
    ws = _ws(tmp_path)
    (ws / "existing.txt").write_text("old", encoding="utf-8")
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    result = handler({"path": "existing.txt", "content": "new"})
    assert "字符" in result.output
    assert (ws / "existing.txt").read_text(encoding="utf-8") == "new"


def test_write_file_creates_parent_dirs(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    result = handler({"path": "a/b/c/deep.txt", "content": "deep"})
    assert "字符" in result.output
    assert (ws / "a/b/c/deep.txt").read_text(encoding="utf-8") == "deep"


def test_write_file_path_traversal_blocked(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(PermissionError):
        handler({"path": "../outside.txt", "content": "nope"})


def test_write_file_null_byte_path(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(ValueError, match="空字节|null"):
        handler({"path": "bad\x00.txt", "content": "nope"})


def test_write_file_empty_path(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供 path"):
        handler({"path": "", "content": "x"})


def test_write_file_null_path(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": None, "content": "x"})


def test_write_file_null_content(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": "x.txt", "content": None})


def test_write_file_char_count(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    chinese = "你好世界"
    result = handler({"path": "chinese.txt", "content": chinese})
    assert f"已将 {len(chinese)} 字符写入" in result.output


# ══════════════════════════════════════════════════════════════════
# glob
# ══════════════════════════════════════════════════════════════════

def test_glob_matches_files(tmp_path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("")
    (ws / "b.py").write_text("")
    (ws / "c.txt").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.py"})
    lines = result.output.splitlines()
    assert len(lines) == 2
    assert all(p.endswith(".py") for p in lines)
    assert not any(p.startswith(str(ws)) for p in lines)


def test_glob_empty_result(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.xyz"})
    assert result.output == ""


def test_glob_returns_relative_paths(tmp_path):
    ws = _ws(tmp_path)
    (ws / "file.py").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.py"})
    assert result.output == "file.py"
    assert not result.output.startswith("\\")


def test_glob_recursive_double_star(tmp_path):
    ws = _ws(tmp_path)
    (ws / "sub").mkdir()
    (ws / "sub" / "deep.py").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "**/*.py"})
    assert "sub" in result.output and "deep.py" in result.output


def test_glob_null_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"pattern": None})


def test_glob_empty_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供 pattern"):
        handler({"pattern": ""})


def test_glob_truncation(tmp_path):
    ws = _ws(tmp_path)
    for i in range(1005):
        (ws / f"file{i}.py").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.py"})
    lines = result.output.splitlines()
    assert len(lines) <= 1001
    assert "截断" in result.output


def test_glob_blocks_path_traversal(tmp_path):
    ws = _ws(tmp_path)
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "leak.txt").write_text("sneaky")
    (ws / "safe.txt").write_text("ok")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "../*"})
    assert "safe.txt" not in result.output
    assert "outside_secret" not in result.output
    assert "leak.txt" not in result.output


# ══════════════════════════════════════════════════════════════════
# patch
# ══════════════════════════════════════════════════════════════════

def test_patch_single_occurrence(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello world hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    handler({"path": "f.txt", "old_str": "world", "new_str": "there"})
    content = (ws / "f.txt").read_text(encoding="utf-8-sig")
    assert content == "hello there hello"


def test_patch_old_str_not_found(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(ValueError, match="未找到字符串"):
        handler({"path": "f.txt", "old_str": "xyz", "new_str": "abc"})


def test_patch_multiple_occurrences(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("a a a", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(ValueError, match="请添加更多上下文"):
        handler({"path": "f.txt", "old_str": "a", "new_str": "b"})


def test_patch_old_str_null(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": "f.txt", "old_str": None, "new_str": "x"})


def test_patch_old_str_empty(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供 old_str"):
        handler({"path": "f.txt", "old_str": "", "new_str": "x"})


def test_patch_new_str_null(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": "f.txt", "old_str": "h", "new_str": None})


def test_patch_new_str_empty_allowed(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    handler({"path": "f.txt", "old_str": "h", "new_str": ""})
    content = (ws / "f.txt").read_text(encoding="utf-8-sig")
    assert content == "ello"


def test_patch_file_not_found(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(FileNotFoundError):
        handler({"path": "nope.txt", "old_str": "x", "new_str": "y"})


def test_patch_path_traversal(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(PermissionError):
        handler({"path": "../outside.txt", "old_str": "x", "new_str": "y"})


def test_patch_char_count(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("你好世界", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    result = handler({"path": "f.txt", "old_str": "你好", "new_str": "hello"})
    after = (ws / "f.txt").read_text(encoding="utf-8")
    assert f"已将 {len(after)} 字符写入" in result.output


def test_patch_rejects_large_file(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "big.py"
    big_content = "x = 1\n" * 200_000
    f.write_text(big_content, encoding="utf-8")
    assert f.stat().st_size > 1_048_576
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(ValueError, match="文件过大"):
        handler({"path": "big.py", "old_str": "x = 1", "new_str": "y = 1"})


# ══════════════════════════════════════════════════════════════════
# grep
# ══════════════════════════════════════════════════════════════════

def test_grep_regex_match(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello 42 world\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"\d+"})
    assert "42" in result.output


def test_grep_regex_not_substring(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"^wor"})
    assert "world" in result.output


def test_grep_invalid_regex(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"["})
    assert "正则表达式格式错误" in result.output


def test_grep_multiple_matches_in_file(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("import os\nimport sys\n\nx=1\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"import"})
    lines = result.output.splitlines()
    assert len(lines) == 2


def test_grep_matches_across_files(tmp_path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("print('hello')\n", encoding="utf-8")
    (ws / "b.py").write_text("print('world')\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"print"})
    lines = result.output.splitlines()
    assert len(lines) == 2


def test_grep_include_filter(tmp_path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "a.txt").write_text("x = 1\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"x = 1", "include": "*.py"})
    assert "a.py" in result.output
    assert "a.txt" not in result.output


def test_grep_path_specified(tmp_path):
    ws = _ws(tmp_path)
    sub = ws / "sub"
    sub.mkdir()
    (ws / "root.txt").write_text("target\n", encoding="utf-8")
    (sub / "deep.txt").write_text("target\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"target", "path": "sub"})
    assert "root" not in result.output
    assert "deep" in result.output


def test_grep_path_traversal(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    with pytest.raises(PermissionError):
        handler({"pattern": r"x", "path": "../outside"})


def test_grep_null_path_uses_root(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("content\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"content", "path": None})
    assert "content" in result.output


def test_grep_null_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"pattern": None})


def test_grep_empty_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    with pytest.raises(ValueError, match="pattern"):
        handler({"pattern": ""})


def test_grep_returns_relative_paths(tmp_path):
    ws = _ws(tmp_path)
    ws_str = str(ws.resolve())
    (ws / "a.txt").write_text("content\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"content"})
    assert ws_str not in result.output, f"absolute workspace path leaked: {result}"
    assert "a.txt" in result.output


def test_grep_root_level_file_no_dot_prefix(tmp_path):
    ws = _ws(tmp_path)
    root_file = ws / "root.py"
    root_file.write_text("line1\nline2\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"line"})
    assert ".:" not in result.output
    assert "1: line1" in result.output or "root.py" in result.output


def test_grep_skips_binary(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("hello\n", encoding="utf-8")
    (ws / "data.bin").write_bytes(b'\x00\x01\x02\xff')
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"hello"})
    assert "hello" in result.output


def test_grep_output_truncation(tmp_path):
    ws = _ws(tmp_path)
    for i in range(100):
        (ws / f"f{i}.py").write_text(f"line1\nline2\nline3\nline4\nline5\nx_{i}\n", encoding="utf-8")
    import vague_code.agent.tools.fs as tmod
    orig = tmod.MAX_GREP_RESULTS
    try:
        tmod.MAX_GREP_RESULTS = 10
        handler = DEFAULT_TOOLS["grep"].bind(str(ws))
        result = handler({"pattern": r"^x_"})
        lines = result.output.splitlines()
        assert len(lines) <= 12
        assert "截断" in result.output
    finally:
        tmod.MAX_GREP_RESULTS = orig


def test_grep_skips_large_file(tmp_path):
    ws = _ws(tmp_path)
    (ws / "small.py").write_text("keep me", encoding="utf-8")
    big = ws / "big.py"
    big.write_bytes(b"x" * 5_000_000)
    import vague_code.agent.tools.fs as tmod
    orig_size = tmod.MAX_GREP_FILE_SIZE
    orig_count = tmod.MAX_GREP_FILE_COUNT
    try:
        tmod.MAX_GREP_FILE_SIZE = 100
        tmod.MAX_GREP_FILE_COUNT = 100
        handler = DEFAULT_TOOLS["grep"].bind(str(ws))
        result = handler({"pattern": r"keep"})
        assert "keep" in result.output
        assert "big.py" not in result.output
    finally:
        tmod.MAX_GREP_FILE_SIZE = orig_size
        tmod.MAX_GREP_FILE_COUNT = orig_count


def test_grep_file_count_truncation(tmp_path):
    ws = _ws(tmp_path)
    for i in range(600):
        (ws / f"f{i}.py").write_text(f"x_{i}\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"^x_"})
    assert "截断" in result.output  # rg --max-files / 结果条数上限
    assert len(result.output.splitlines()) < 600


# ══════════════════════════════════════════════════════════════════
# bash
# ══════════════════════════════════════════════════════════════════

def test_bash_simple_command(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo hello"})
    assert "hello" in result.output
    assert "退出码: 0" in result.output


def test_bash_exit_code_in_output(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo hello"})
    assert "退出码: 0" in result.output


def test_bash_nonzero_exit_code(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "cmd /c exit 1"})
    assert "退出码: 1" in result.output


def test_bash_stderr_captured(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo error 1>&2"})
    assert "标准错误输出" in result.output
    assert "error" in result.output


def test_bash_timeout_raises(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))

    class FakePopen:
        pid = 12345
        _call_count = 0
        def __init__(self, *args, **kwargs):
            self.stdout = None
            self.stderr = None
        def communicate(self, timeout=None):
            FakePopen._call_count += 1
            if FakePopen._call_count == 1:
                raise subprocess.TimeoutExpired(cmd="echo", timeout=0.001, output=b"")
            return b"", b""
        def kill(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    with pytest.raises(RuntimeError, match="超时"):
        handler({"command": "echo hi"})


def test_bash_output_truncation(tmp_path):
    ws = _ws(tmp_path)
    (ws / "big.txt").write_text("x" * 60_000, encoding="utf-8")
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    cmd = "type big.txt" if os.name == "nt" else "cat big.txt"
    result = handler({"command": cmd})
    assert result.metadata["truncated"] is True
    assert len(result.output) < 60_000


def test_bash_cwd_works(tmp_path):
    ws = _ws(tmp_path)
    sub = ws / "subdir"
    sub.mkdir()
    (sub / "marker.txt").write_text("present", encoding="utf-8")
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "dir /b marker.txt", "cwd": "subdir"})
    assert "marker.txt" in result.output


def test_bash_cwd_traversal(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    with pytest.raises(PermissionError):
        handler({"command": "echo hi", "cwd": "../outside"})


def test_bash_null_command(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"command": None})


def test_bash_empty_command(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供 command"):
        handler({"command": ""})


def test_bash_chcp_command_not_doubled(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    import vague_code.agent.tools.bash_tool as bmod
    orig_popen = bmod.subprocess.Popen
    try:
        class FakePopen:
            pid = 999
            returncode = 0
            def __init__(self, *_a, **_k):
                pass
            def communicate(self, timeout=None):
                return (b"hello", b"")
            def kill(self):
                pass
        bmod.subprocess.Popen = FakePopen
        result = handler({"command": "chcp"})
        assert "hello" in result.output
        result2 = handler({"command": "echo hi"})
        assert "hello" in result2.output
    finally:
        bmod.subprocess.Popen = orig_popen


# ── bash 测试结果结构化（plans/0018 #5） ───────────────────────────────

def test_is_test_command():
    assert _is_test_command("python -m pytest tests/test_x.py")
    assert _is_test_command("pytest")
    assert _is_test_command("python -m unittest test_x")
    assert _is_test_command("make test")
    assert not _is_test_command("echo hello")
    assert not _is_test_command("python run.py")
    assert not _is_test_command("")


def test_summarize_pytest_pass():
    out = _summarize_test_output(
        "collected 3 items\n...\n3 passed in 0.5s", "", 0)
    assert out == "[test] PASS (3 passed)"


def test_summarize_pytest_failed():
    out = _summarize_test_output(
        "1 passed, 1 failed in 0.5s", "", 1)
    assert out == "[test] FAIL (1 failed)"


def test_summarize_pytest_error_on_stderr():
    out = _summarize_test_output(
        "", "ERROR: 2 errors in 1.0s", 1)
    assert out == "[test] FAIL (2 error)"


def test_summarize_fallback_exit_code():
    assert _summarize_test_output("nothing here", "", 0) == "[test] PASS (exit 0)"
    assert _summarize_test_output("nothing here", "", 3) == "[test] FAIL (exit 3)"


def test_bash_appends_test_verdict(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo pytest && echo \"2 passed in 1s\""})
    assert "[test] PASS (2 passed)" in result.output


def test_bash_non_test_command_no_verdict(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo hello"})
    assert "[test]" not in result.output

# ?? ?? python -c ?????ADR-0029? ?????????????????????????????????????

def test_multiline_python_c_runs_from_temp_script(tmp_path) -> None:
    import tempfile

    handler = BashTool(str(tmp_path))
    code = (
        "import sys\n"
        "for i in range(3):\n"
        "    print('line', i)\n"
        "print('done')"
    )
    result = handler({"command": f"python -c \"{code}\""})
    assert "line 0" in result.output
    assert "line 1" in result.output
    assert "line 2" in result.output
    assert "done" in result.output
    leftovers = list(Path(tempfile.gettempdir()).glob("vaguecode_*.py"))
    assert not leftovers, "temp scripts must be cleaned up"


def test_single_line_python_c_untouched(tmp_path) -> None:

    handler = BashTool(str(tmp_path))
    result = handler({"command": "python -c \"print('ok')\""})
    assert "ok" in result.output


def test_grep_excludes_noise_dirs(tmp_path) -> None:

    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "log.jsonl").write_text("secret_token_abc", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("secret_token_abc", encoding="utf-8")
    g = GrepTool(str(tmp_path))
    out = g({"pattern": "secret_token_abc", "path": "."})
    assert "runs" not in out.output
    assert "a.py" in out.output


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe 交互删除确认是 Windows 专属行为")
def test_bash_interactive_confirm_gets_guidance(tmp_path) -> None:

    ws = tmp_path
    (ws / "tmp_a.py").write_text("print(1)", encoding="utf-8")
    (ws / "tmp_b.py").write_text("print(2)", encoding="utf-8")
    (ws / "src").mkdir()
    handler = BashTool(str(ws))
    result = handler(
        {"command": "del tmp_a.py tmp_b.py 2>&1; python -m ruff check src"}
    )
    assert "交互确认提示" in result.output
    assert "del /Q" in result.output or "os.remove" in result.output
    assert "cmd.exe" in result.output or ";" in result.output


def test_bash_normal_output_unaffected(tmp_path) -> None:

    handler = BashTool(str(tmp_path))
    result = handler({"command": "echo hello"})
    assert "交互确认提示" not in result.output
    assert "hello" in result.output


def test_bash_spec_describes_cmd_exe() -> None:
    from vague_code.agent.tools import BashTool

    assert "cmd.exe" in BashTool.spec().description
    assert "&" in BashTool.spec().description
    assert "del /Q" in BashTool.spec().description



# ── Did you mean?（opencode read miss 范式，ADR-0004 重构） ──────────────

def test_read_file_not_found_suggests_similar(tmp_path):
    ws = _ws(tmp_path)
    (ws / "app_client.py").write_text("x", encoding="utf-8")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(FileNotFoundError, match="您是不是要找"):
        handler({"path": "client.py"})


def test_read_file_not_found_no_suggestion(tmp_path):
    ws = _ws(tmp_path)
    (ws / "main.py").write_text("x", encoding="utf-8")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(FileNotFoundError) as exc:
        handler({"path": "totally_absent.py"})
    assert "您是不是要找" not in str(exc.value)


def test_patch_not_found_suggests_similar(tmp_path):
    ws = _ws(tmp_path)
    (ws / "app_client.py").write_text("x", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    with pytest.raises(FileNotFoundError, match="您是不是要找"):
        handler({"path": "client.py", "old_str": "x", "new_str": "y"})


# ── read: offset/limit + 目录 + 二进制检测（plans/0019） ──────────────

def test_read_file_offset_limit(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("".join(f"line{i}\n" for i in range(20)), encoding="utf-8")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    r = handler({"path": "f.txt", "offset": 5, "limit": 3})
    assert "第 5-7 行" in r.output
    assert "line4" in r.output and "line6" in r.output
    assert "line0" not in r.output


def test_read_file_offset_beyond_eof(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("a\nb\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    r = handler({"path": "f.txt", "offset": 10})
    assert "无内容" in r.output


def test_read_file_directory_lists_sorted(tmp_path):
    ws = _ws(tmp_path)
    (ws / "b.py").write_text("x", encoding="utf-8")
    (ws / "a.py").write_text("x", encoding="utf-8")
    (ws / "sub").mkdir()
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    r = handler({"path": "."})
    assert "目录 ." in r.output
    names = [line for line in r.output.splitlines()[1:] if line]
    assert names[0] == "a.py"
    assert "b.py" in names
    assert "sub/" in names


def test_read_file_binary_skipped(tmp_path):
    ws = _ws(tmp_path)
    (ws / "data.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    r = handler({"path": "data.bin"})
    assert "二进制文件" in r.output


def test_read_file_binary_by_extension(tmp_path):
    ws = _ws(tmp_path)
    (ws / "pkg.zip").write_bytes(b"PK\x05\x06not really zip")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    r = handler({"path": "pkg.zip"})
    assert "二进制文件" in r.output


def test_read_file_long_line_truncated(tmp_path):
    ws = _ws(tmp_path)
    (ws / "long.txt").write_text("x" * 5000 + "\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    r = handler({"path": "long.txt"})
    assert "行截断至" in r.output
    assert len(r.output.splitlines()[-1]) <= 2100


# ── glob: path 参数 + 确定性排序（plans/0019） ─────────────────────────

def test_glob_sorted_deterministic(tmp_path):
    ws = _ws(tmp_path)
    for name in ("b.py", "a.py", "c.py"):
        (ws / name).write_text("x", encoding="utf-8")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    r = handler({"pattern": "*.py"}).output
    lines = r.splitlines()
    assert lines == ["a.py", "b.py", "c.py"]
    assert handler({"pattern": "*.py"}).output == r  # 两次结果一致


def test_glob_path_param(tmp_path):
    ws = _ws(tmp_path)
    (ws / "sub").mkdir()
    (ws / "sub" / "x.py").write_text("x", encoding="utf-8")
    (ws / "root.py").write_text("x", encoding="utf-8")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    r = handler({"pattern": "*.py", "path": "sub"}).output.replace("\\", "/")
    assert r == "sub/x.py"
    assert "root.py" not in r


def test_glob_path_not_directory(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("x", encoding="utf-8")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    with pytest.raises(ValueError, match="不是目录"):
        handler({"pattern": "*.py", "path": "f.txt"})


# ── 原子写（plans/0019） ──────────────────────────────────────────────

def test_write_atomic_no_temp_leftover(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    handler({"path": "f.txt", "content": "content"})
    leftovers = list(ws.glob(".vaguecode_*"))
    assert leftovers == []
    assert (ws / "f.txt").read_text(encoding="utf-8") == "content"


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限语义仅类 Unix 有效")
def test_write_atomic_preserves_mode(tmp_path):
    import stat as _stat
    ws = _ws(tmp_path)
    f = ws / "f.txt"
    f.write_text("old", encoding="utf-8")
    os.chmod(f, 0o600)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    handler({"path": "f.txt", "content": "new"})
    assert _stat.S_IMODE(f.stat().st_mode) == 0o600


def test_patch_atomic_writes(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("a\nb\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["patch"].bind(str(ws))
    handler({"path": "f.py", "old_str": "a", "new_str": "A"})
    assert (ws / "f.py").read_text(encoding="utf-8") == "A\nb\n"
    assert list(ws.glob(".vaguecode_*")) == []


# ── grep: ripgrep 参数扩展（plans/0019） ──────────────────────────────

def test_grep_ignore_case(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("Hello World\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    assert handler({"pattern": "hello"}).output == ""
    assert "Hello" in handler({"pattern": "hello", "ignore_case": True}).output


def test_grep_literal(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("a.b.c\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    assert "a.b.c" in handler({"pattern": "a.b.c", "literal": True}).output
    # 正则语义下 . 匹配任意字符——literal 应只匹配字面
    assert handler({"pattern": "aXbXc", "literal": True}).output == ""


def test_grep_context_lines(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("before\nMATCH\nafter\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    r = handler({"pattern": "MATCH", "context": 1}).output
    assert "before" in r and "after" in r


def test_grep_excludes_noise_dirs_via_rg(tmp_path):
    """rg 路径：EXCLUDED_DIRS 硬编码噪音目录排除（runs 等非 gitignore 项）。"""
    ws = _ws(tmp_path)
    (ws / "runs").mkdir()
    (ws / "runs" / "log.jsonl").write_text("needle", encoding="utf-8")
    (ws / "keep.py").write_text("needle", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    r = handler({"pattern": "needle"}).output
    assert "runs" not in r
    assert "keep.py" in r


def test_grep_long_line_truncated(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("x" * 2000 + "\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    r = handler({"pattern": "x"}).output
    assert "行截断至" in r


def test_grep_python_fallback_when_no_rg(tmp_path, monkeypatch):
    """rg 不可用 → 纯 Python 降级保底（行为一致）。"""
    import vague_code.agent.tools.fs as tmod
    monkeypatch.setattr(tmod, "_rg_path", lambda: None)
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("needle here\na.b.c\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    assert "needle" in handler({"pattern": "needle"}).output
    assert "needle" in handler({"pattern": "NEEDLE", "ignore_case": True}).output
    assert "a.b" in handler({"pattern": "a.b", "literal": True}).output


# ── bash: timeout 参数 + 输出落盘（plans/0019） ───────────────────────

def test_bash_timeout_param_used(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    seen: dict = {}

    def fake_communicate(self, timeout=None):
        seen["timeout"] = timeout
        return (b"", b"")

    def fake_popen(*args, **kwargs):
        import types
        p = types.SimpleNamespace()
        p.communicate = fake_communicate.__get__(p)
        p.returncode = 0
        p.pid = 1
        p.kill = lambda: None
        return p

    import vague_code.agent.tools.bash_tool as bmod
    monkeypatch.setattr(bmod.subprocess, "Popen", fake_popen)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    handler({"command": "echo hi", "timeout": 7})
    assert seen.get("timeout") == 7
    handler({"command": "echo hi"})
    assert seen.get("timeout") == 30  # 默认


def test_bash_output_spilled_to_file(tmp_path):
    ws = _ws(tmp_path)
    (ws / "big.txt").write_text("x" * 60_000, encoding="utf-8")
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "type big.txt"})
    assert result.metadata["truncated"] is True
    full_path = result.metadata.get("full_output_path")
    assert full_path is not None
    from pathlib import Path as _P
    assert len(_P(full_path).read_text(encoding="utf-8")) > 50_000

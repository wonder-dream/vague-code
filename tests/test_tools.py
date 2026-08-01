from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.agent.tools import DEFAULT_TOOLS


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
    assert result == "hello world"


def test_read_file_not_found(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(FileNotFoundError):
        handler({"path": "nope.txt"})


def test_read_file_empty_path(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供路径"):
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
    big.write_text("A" * 2000, encoding="utf-8")
    import src.agent.tools as tmod
    orig = tmod.MAX_READ_BYTES
    try:
        tmod.MAX_READ_BYTES = 1000
        handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
        result = handler({"path": "big.txt"})
        assert "截断" in result
        assert "文件总大小" in result
    finally:
        tmod.MAX_READ_BYTES = orig


def test_read_file_strips_utf8_bom(tmp_path):
    ws = _ws(tmp_path)
    (ws / "bom.txt").write_bytes(b'\xef\xbb\xbf{"key": "value"}')
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    result = handler({"path": "bom.txt"})
    assert result.startswith('{"key"')
    assert "\ufeff" not in result


def test_read_file_byte_truncation_chinese(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "chinese.txt"
    chinese_chars = "中" * 5000
    f.write_text(chinese_chars, encoding="utf-8")
    import src.agent.tools as tmod
    orig = tmod.MAX_READ_BYTES
    try:
        tmod.MAX_READ_BYTES = 100
        handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
        result = handler({"path": "chinese.txt"})
        assert "截断于" in result
        assert "100" in result
    finally:
        tmod.MAX_READ_BYTES = orig


# ══════════════════════════════════════════════════════════════════
# write_file
# ══════════════════════════════════════════════════════════════════

def test_write_file_creates_file(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    result = handler({"path": "new.txt", "content": "hello"})
    assert "字符" in result
    assert (ws / "new.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_overwrite_false_rejects(tmp_path):
    ws = _ws(tmp_path)
    (ws / "existing.txt").write_text("old", encoding="utf-8")
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    with pytest.raises(FileExistsError):
        handler({"path": "existing.txt", "content": "new"})


def test_write_file_overwrite_true_succeeds(tmp_path):
    ws = _ws(tmp_path)
    (ws / "existing.txt").write_text("old", encoding="utf-8")
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    result = handler({"path": "existing.txt", "content": "new", "overwrite": True})
    assert "字符" in result
    assert (ws / "existing.txt").read_text(encoding="utf-8") == "new"


def test_write_file_creates_parent_dirs(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["write_file"].bind(str(ws))
    result = handler({"path": "a/b/c/deep.txt", "content": "deep"})
    assert "字符" in result
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
    with pytest.raises(ValueError, match="需要提供路径"):
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
    assert f"已将 {len(chinese)} 字符写入" in result


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
    lines = result.splitlines()
    assert len(lines) == 2
    assert all(p.endswith(".py") for p in lines)
    assert not any(p.startswith(str(ws)) for p in lines)


def test_glob_empty_result(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.xyz"})
    assert result == ""


def test_glob_returns_relative_paths(tmp_path):
    ws = _ws(tmp_path)
    (ws / "file.py").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.py"})
    assert result == "file.py"
    assert not result.startswith("\\")


def test_glob_recursive_double_star(tmp_path):
    ws = _ws(tmp_path)
    (ws / "sub").mkdir()
    (ws / "sub" / "deep.py").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "**/*.py"})
    assert "sub" in result and "deep.py" in result


def test_glob_null_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"pattern": None})


def test_glob_empty_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    with pytest.raises(ValueError, match="需要提供模式"):
        handler({"pattern": ""})


def test_glob_truncation(tmp_path):
    ws = _ws(tmp_path)
    for i in range(1005):
        (ws / f"file{i}.py").write_text("")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "*.py"})
    lines = result.splitlines()
    assert len(lines) <= 1001
    assert "截断" in result


def test_glob_blocks_path_traversal(tmp_path):
    ws = _ws(tmp_path)
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "leak.txt").write_text("sneaky")
    (ws / "safe.txt").write_text("ok")
    handler = DEFAULT_TOOLS["glob"].bind(str(ws))
    result = handler({"pattern": "../*"})
    assert "safe.txt" not in result
    assert "outside_secret" not in result
    assert "leak.txt" not in result


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
    assert f"已将 {len(after)} 字符写入" in result


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
    assert "42" in result


def test_grep_regex_not_substring(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"^wor"})
    assert "world" in result


def test_grep_invalid_regex(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("hello", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"["})
    assert "正则表达式格式错误" in result


def test_grep_multiple_matches_in_file(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.txt").write_text("import os\nimport sys\n\nx=1\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"import"})
    lines = result.splitlines()
    assert len(lines) == 2


def test_grep_matches_across_files(tmp_path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("print('hello')\n", encoding="utf-8")
    (ws / "b.py").write_text("print('world')\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"print"})
    lines = result.splitlines()
    assert len(lines) == 2


def test_grep_include_filter(tmp_path):
    ws = _ws(tmp_path)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "a.txt").write_text("x = 1\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"x = 1", "include": "*.py"})
    assert "a.py" in result
    assert "a.txt" not in result


def test_grep_path_specified(tmp_path):
    ws = _ws(tmp_path)
    sub = ws / "sub"
    sub.mkdir()
    (ws / "root.txt").write_text("target\n", encoding="utf-8")
    (sub / "deep.txt").write_text("target\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"target", "path": "sub"})
    assert "root" not in result
    assert "deep" in result


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
    assert "content" in result


def test_grep_null_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"pattern": None})


def test_grep_empty_pattern(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    with pytest.raises(ValueError, match="非空字符串"):
        handler({"pattern": ""})


def test_grep_returns_relative_paths(tmp_path):
    ws = _ws(tmp_path)
    ws_str = str(ws.resolve())
    (ws / "a.txt").write_text("content\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"content"})
    assert ws_str not in result, f"absolute workspace path leaked: {result}"
    assert "a.txt" in result


def test_grep_root_level_file_no_dot_prefix(tmp_path):
    ws = _ws(tmp_path)
    root_file = ws / "root.py"
    root_file.write_text("line1\nline2\n", encoding="utf-8")
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"line"})
    assert ".:" not in result
    assert "1: line1" in result or "root.py" in result


def test_grep_skips_binary(tmp_path):
    ws = _ws(tmp_path)
    (ws / "f.py").write_text("hello\n", encoding="utf-8")
    (ws / "data.bin").write_bytes(b'\x00\x01\x02\xff')
    handler = DEFAULT_TOOLS["grep"].bind(str(ws))
    result = handler({"pattern": r"hello"})
    assert "hello" in result


def test_grep_output_truncation(tmp_path):
    ws = _ws(tmp_path)
    for i in range(100):
        (ws / f"f{i}.py").write_text(f"line1\nline2\nline3\nline4\nline5\nx_{i}\n", encoding="utf-8")
    import src.agent.tools as tmod
    orig = tmod.MAX_GREP_RESULTS
    try:
        tmod.MAX_GREP_RESULTS = 10
        handler = DEFAULT_TOOLS["grep"].bind(str(ws))
        result = handler({"pattern": r"^x_"})
        lines = result.splitlines()
        assert len(lines) <= 12
        assert "截断" in result
    finally:
        tmod.MAX_GREP_RESULTS = orig


def test_grep_skips_large_file(tmp_path):
    ws = _ws(tmp_path)
    (ws / "small.py").write_text("keep me", encoding="utf-8")
    big = ws / "big.py"
    big.write_bytes(b"x" * 5_000_000)
    import src.agent.tools as tmod
    orig_size = tmod.MAX_GREP_FILE_SIZE
    orig_count = tmod.MAX_GREP_FILE_COUNT
    try:
        tmod.MAX_GREP_FILE_SIZE = 100
        tmod.MAX_GREP_FILE_COUNT = 100
        handler = DEFAULT_TOOLS["grep"].bind(str(ws))
        result = handler({"pattern": r"keep"})
        assert "keep" in result
        assert "big.py" not in result
    finally:
        tmod.MAX_GREP_FILE_SIZE = orig_size
        tmod.MAX_GREP_FILE_COUNT = orig_count


def test_grep_file_count_truncation(tmp_path):
    ws = _ws(tmp_path)
    for i in range(600):
        (ws / f"f{i}.py").write_text(f"x_{i}\n", encoding="utf-8")
    import src.agent.tools as tmod
    orig_size = tmod.MAX_GREP_FILE_SIZE
    orig_count = tmod.MAX_GREP_FILE_COUNT
    try:
        tmod.MAX_GREP_FILE_SIZE = 10_000_000
        tmod.MAX_GREP_FILE_COUNT = 50
        handler = DEFAULT_TOOLS["grep"].bind(str(ws))
        result = handler({"pattern": r"^x_"})
        assert "已截断于 50 个文件" in result
    finally:
        tmod.MAX_GREP_FILE_SIZE = orig_size
        tmod.MAX_GREP_FILE_COUNT = orig_count


# ══════════════════════════════════════════════════════════════════
# bash
# ══════════════════════════════════════════════════════════════════

def test_bash_simple_command(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo hello"})
    assert "hello" in result
    assert "退出码: 0" in result


def test_bash_exit_code_in_output(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo hello"})
    assert "退出码: 0" in result


def test_bash_nonzero_exit_code(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "cmd /c exit 1"})
    assert "退出码: 1" in result


def test_bash_stderr_captured(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "echo error 1>&2"})
    assert "标准错误输出" in result
    assert "error" in result


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
    import src.agent.tools as tmod
    orig = tmod.MAX_OUTPUT
    try:
        tmod.MAX_OUTPUT = 10
        handler = DEFAULT_TOOLS["bash"].bind(str(ws))
        result = handler({"command": "echo hello world"})
        assert "截断" in result
    finally:
        tmod.MAX_OUTPUT = orig


def test_bash_cwd_works(tmp_path):
    ws = _ws(tmp_path)
    sub = ws / "subdir"
    sub.mkdir()
    (sub / "marker.txt").write_text("present", encoding="utf-8")
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    result = handler({"command": "dir /b marker.txt", "cwd": "subdir"})
    assert "marker.txt" in result


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
    with pytest.raises(ValueError, match="需要提供命令"):
        handler({"command": ""})


def test_bash_chcp_command_not_doubled(tmp_path):
    ws = _ws(tmp_path)
    handler = DEFAULT_TOOLS["bash"].bind(str(ws))
    import src.agent.tools as tmod
    orig_popen = tmod.subprocess.Popen
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
        tmod.subprocess.Popen = FakePopen
        result = handler({"command": "chcp"})
        assert "hello" in result
        result2 = handler({"command": "echo hi"})
        assert "hello" in result2
    finally:
        tmod.subprocess.Popen = orig_popen
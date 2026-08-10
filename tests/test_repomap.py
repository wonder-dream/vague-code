from __future__ import annotations

import time
from pathlib import Path

from vague_code.agent.repomap import RepoIndex, _extract_symbols

SRC = """
import os

class Stats:
    def mean(self, xs):
        return sum(xs) / len(xs)

    def median(self, xs):
        return sorted(xs)[len(xs) // 2]

def calculate(denominator):
    return 1 / denominator

def unused_helper():
    pass
"""


def _write(d: Path, rel: str, content: str) -> None:
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_index(d: Path) -> RepoIndex:
    idx = RepoIndex(workdir=str(d))
    idx.build()
    return idx


# ── _extract_symbols ───────────────────────────────────────────────────────

def test_extract_symbols_classes_functions_methods() -> None:
    syms, counts = _extract_symbols(SRC, "stats.py")
    by_name = {s.name: s for s in syms}
    assert by_name["Stats"].kind == "class"
    assert by_name["mean"].kind == "method"
    assert by_name["median"].kind == "method"
    assert by_name["calculate"].kind == "function"
    assert by_name["calculate"].line == 11
    assert "def calculate(denominator):" in by_name["calculate"].signature
    assert counts["Stats"] == 1  # class name counted as identifier


def test_extract_symbols_invalid_python_no_crash() -> None:
    syms, counts = _extract_symbols("def broken(:\n  class", "bad.py")
    assert isinstance(syms, list)
    assert isinstance(counts, dict)


# ── RepoIndex.build ────────────────────────────────────────────────────────

def test_build_indexes_py_files(tmp_path: Path) -> None:
    _write(tmp_path, "stats.py", SRC)
    _write(tmp_path, "nested/util.py", "def helper():\n    pass\n")
    _write(tmp_path, "notes.txt", "not python")  # ignored
    idx = _make_index(tmp_path)
    assert idx.size == 6  # Stats + mean + median + calculate + unused_helper + helper


def test_build_ignores_non_python_and_hidden(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def a():\n    pass\n")
    _write(tmp_path, ".hidden/b.py", "def b():\n    pass\n")
    _write(tmp_path, "__pycache__/c.py", "def c():\n    pass\n")
    idx = _make_index(tmp_path)
    names = {s.name for s in idx._symbols}
    assert names == {"a"}


def test_build_empty_dir(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    assert idx.size == 0
    assert idx.to_map_text() == ""


def test_build_nonexistent_workdir(tmp_path: Path) -> None:
    idx = RepoIndex(workdir=str(tmp_path / "missing"))
    idx.build()
    assert idx.size == 0


def test_build_max_files_limit(tmp_path: Path) -> None:
    for i in range(5):
        _write(tmp_path, f"f{i}.py", f"def fn{i}():\n    pass\n")
    idx = RepoIndex(workdir=str(tmp_path), max_files=2)
    idx.build()
    assert idx.size == 2


# ── search ────────────────────────────────────────────────────────────────

def test_search_by_substring(tmp_path: Path) -> None:
    _write(tmp_path, "stats.py", SRC)
    idx = _make_index(tmp_path)
    results = idx.search("calc")
    assert len(results) == 1
    assert results[0].name == "calculate"


def test_search_regex(tmp_path: Path) -> None:
    _write(tmp_path, "stats.py", SRC)
    idx = _make_index(tmp_path)
    results = idx.search(r"^me", k=20)
    names = {s.name for s in results}
    assert names == {"mean", "median"}


def test_search_path_filter(tmp_path: Path) -> None:
    _write(tmp_path, "stats.py", SRC)
    _write(tmp_path, "other.py", "def calculate():\n    pass\n")
    idx = _make_index(tmp_path)
    results = idx.search("calculate", path="stats")
    assert len(results) == 1
    assert results[0].file == "stats.py"


def test_search_empty_query(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def a():\n    pass\n")
    idx = _make_index(tmp_path)
    assert idx.search("") == []


def test_search_invalid_regex(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def a():\n    pass\n")
    idx = _make_index(tmp_path)
    assert idx.search("(") == []


def test_search_before_build_returns_empty() -> None:
    idx = RepoIndex(workdir=".")
    assert idx.search("anything") == []


# ── top_symbols / to_map_text ─────────────────────────────────────────────

def test_top_symbols_ranked_by_ref_count(tmp_path: Path) -> None:
    _write(tmp_path, "stats.py", SRC)
    _write(tmp_path, "main.py", "from stats import calculate\ncalculate(2)\ncalculate(3)\n")
    idx = _make_index(tmp_path)
    tops = idx.top_symbols(5)
    assert tops[0].name == "calculate"  # referenced in two files


def test_to_map_text_contains_signatures(tmp_path: Path) -> None:
    _write(tmp_path, "stats.py", SRC)
    idx = _make_index(tmp_path)
    text = idx.to_map_text(max_tokens=500)
    assert "stats.py:11: def calculate(denominator):" in text
    assert "class Stats:" in text


def test_to_map_text_respects_token_budget(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def fn_%d():\n    pass\n" % 0 + "".join(
        f"def fn_{i}():\n    pass\n" for i in range(200)
    ))
    idx = _make_index(tmp_path)
    text = idx.to_map_text(max_tokens=100)
    assert len(text) <= 100 * 4 + 500  # rough budget (line granularity)


# ── refresh ───────────────────────────────────────────────────────────────

def test_refresh_detects_modified_file(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def foo():\n    pass\n")
    idx = _make_index(tmp_path)
    assert {s.name for s in idx._symbols} == {"foo"}

    _write(tmp_path, "a.py", "def foo():\n    pass\n\ndef bar():\n    pass\n")
    changed = idx.refresh()
    assert changed == ["a.py"]
    names = {s.name for s in idx._symbols}
    assert names == {"foo", "bar"}


def test_refresh_no_change_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def foo():\n    pass\n")
    idx = _make_index(tmp_path)
    time.sleep(0.01)
    assert idx.refresh() == []


def test_refresh_specific_paths(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "def foo():\n    pass\n")
    _write(tmp_path, "b.py", "def bar():\n    pass\n")
    idx = _make_index(tmp_path)
    _write(tmp_path, "b.py", "def bar2():\n    pass\n")
    changed = idx.refresh(paths=["b.py"])
    assert changed == ["b.py"]
    assert {s.name for s in idx._symbols} == {"foo", "bar2"}


# ── make_code_search_handler / code_search tool ───────────────────────────

def test_code_search_handler_end_to_end(tmp_path: Path) -> None:
    from vague_code.agent.tools import make_code_search_handler

    _write(tmp_path, "stats.py", SRC)
    idx = _make_index(tmp_path)
    handler = make_code_search_handler(idx)

    out = handler({"query": "calculate"})
    assert "stats.py:11: def calculate" in out

    assert "未找到" in handler({"query": "nonexistent_symbol_xyz"})
    assert "需要提供搜索查询" in handler({"query": ""})
    assert "未找到" in handler({"query": "("})  # invalid regex handled

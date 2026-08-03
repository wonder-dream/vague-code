from __future__ import annotations

import subprocess
from pathlib import Path

from eval.env import EnvSpec
from eval.verify import (
    apply_test_patch,
    classify_pytest,
    diff_empty,
    parse_test_paths,
    reset_test_files,
    reset_workdir,
    verify_run,
)

APP = "def add(a, b):\n    return a + b\n"
TEST_BASE = "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"

# test_patch 把 test_add 改成期望 4（base 上必失败 → 有效 F2P 判别器）
TEST_PATCH = """diff --git a/test_app.py b/test_app.py
--- a/test_app.py
+++ b/test_app.py
@@ -3,2 +3,2 @@
 def test_add():
-    assert add(1, 2) == 3
+    assert add(1, 2) == 4
"""


def _git(d: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True)


def _init_repo(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "test@x")
    _git(d, "config", "user.name", "test")
    _git(d, "config", "core.autocrlf", "false")
    (d / "app.py").write_text(APP, encoding="utf-8", newline="\n")
    (d / "test_app.py").write_text(TEST_BASE, encoding="utf-8", newline="\n")
    _git(d, "add", ".")
    _git(d, "commit", "-qm", "base")


def _env(d: Path) -> EnvSpec:
    return EnvSpec(venv_dir=d / "venv", python=d / "python",
                   repo_key="r", repo="r")


# ── parse_test_paths ─────────────────────────────────────────────────────

def test_parse_test_paths_extracts_b_paths() -> None:
    assert parse_test_paths(TEST_PATCH) == ["test_app.py"]
    assert parse_test_paths("") == []
    assert parse_test_paths("diff --git a/a.py b/a.py\n") == ["a.py"]


# ── classify_pytest（P0-4：断言失败 vs collection error） ────────────────

def test_classify_pass() -> None:
    assert classify_pytest(0, "1 passed in 0.1s") == "pass"


def test_classify_fail_assertion() -> None:
    out = "________ test_add ________\nassert 3 == 4\n1 failed in 0.1s"
    assert classify_pytest(1, out) == "fail"


def test_classify_collection_error() -> None:
    out = "ERROR collecting test_app.py\nModuleNotFoundError: No module named 'nope'"
    assert classify_pytest(2, out) == "collection_error"


def test_classify_no_tests() -> None:
    assert classify_pytest(5, "no tests ran in 0.0s") == "no_tests"


# ── apply / reset test files（P0-3） ─────────────────────────────────────

def test_apply_and_reset_test_files() -> None:
    d = Path("tmp_vr_repo")
    _init_repo(d)
    try:
        # base 上 apply test_patch → 测试文件被改
        apply_test_patch(d, TEST_PATCH)
        assert "== 4" in (d / "test_app.py").read_text(encoding="utf-8")

        # Agent 篡改测试文件（钻空子）
        (d / "test_app.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
        # P0-3：回滚 test_patch 覆盖的文件 → apply
        reset_test_files(d, parse_test_paths(TEST_PATCH))
        assert TEST_BASE == (d / "test_app.py").read_text(encoding="utf-8")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ── diff_empty / reset_workdir（P0-2） ───────────────────────────────────

def test_diff_empty_true_on_clean() -> None:
    d = Path("tmp_vr_clean")
    _init_repo(d)
    try:
        assert diff_empty(d) is True
        (d / "app.py").write_text("x\n", encoding="utf-8")
        assert diff_empty(d) is False
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_reset_workdir_removes_changes_and_untracked() -> None:
    d = Path("tmp_vr_reset")
    _init_repo(d)
    try:
        (d / "app.py").write_text("changed\n", encoding="utf-8")
        (d / "junk.txt").write_text("junk", encoding="utf-8")
        reset_workdir(d)
        assert (d / "app.py").read_text(encoding="utf-8") == APP
        assert not (d / "junk.txt").exists()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ── verify_run（P0-1） ───────────────────────────────────────────────────

def test_verify_no_diff(monkeypatch) -> None:
    d = Path("tmp_vr_nodiff")
    _init_repo(d)
    try:
        env = _env(d)
        r = verify_run({"test_patch": TEST_PATCH,
                        "FAIL_TO_PASS": ["test_app.py::test_add"],
                        "PASS_TO_PASS": []}, d, env)
        assert r.verified is False and r.reason == "no_diff"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


class _Run:
    def __init__(self, node_id, state):
        self.node_id = node_id
        self.state = state
        self.output = ""


def test_verify_f2p_pass(monkeypatch) -> None:
    d = Path("tmp_vr_pass")
    _init_repo(d)
    try:
        (d / "app.py").write_text("def add(a, b):\n    return 4\n", encoding="utf-8")
        from eval import verify
        monkeypatch.setattr(verify, "run_node_ids",
                            lambda env, wd, ids, timeout_s=600: [_Run(i, "pass") for i in ids])
        r = verify_run({"test_patch": TEST_PATCH,
                        "FAIL_TO_PASS": ["test_app.py::test_add"],
                        "PASS_TO_PASS": ["test_app.py::test_add"]}, d, _env(d))
        assert r.verified is True and r.f2p_pass is True and r.p2p_pass is True
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_verify_f2p_fail(monkeypatch) -> None:
    d = Path("tmp_vr_fail")
    _init_repo(d)
    try:
        (d / "app.py").write_text("def add(a, b):\n    return 5\n", encoding="utf-8")
        from eval import verify
        monkeypatch.setattr(verify, "run_node_ids",
                            lambda env, wd, ids, timeout_s=600: [_Run(i, "fail") for i in ids])
        r = verify_run({"test_patch": TEST_PATCH,
                        "FAIL_TO_PASS": ["test_app.py::test_add"],
                        "PASS_TO_PASS": []}, d, _env(d))
        assert r.verified is False and r.f2p_pass is False
        assert r.reason == "f2p:fail"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

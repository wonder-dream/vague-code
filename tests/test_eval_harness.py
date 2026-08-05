from __future__ import annotations

import json
from pathlib import Path

from eval.harness import (
    _run_cost,
    _run_db_path,
    _venv_lock_sha1,
    load_manifest,
    mark_done,
    save_manifest,
)
from eval.matrix import EvalCell, TaskResult, cell_label, parse_cell_label


def _cell(compression: bool = True, concurrency: bool = False,
          repo_map: bool = True, repeat: int = 2) -> EvalCell:
    return EvalCell(compression=compression, concurrency=concurrency,
                    repo_map=repo_map, repeat=repeat)


# ── P0-7: 每 run 独立 db（离线判题/指标/judge 的定位基础） ─────────────

def _rel_parts(p: str) -> list[str]:
    return list(Path(p).parts[1:])  # 去掉 runs/，跨平台

def test_run_db_path_contains_instance_and_cell() -> None:
    p = _run_db_path("astropy__astropy-14182", _cell())
    parts = _rel_parts(p)
    assert parts[0] == "eval"
    assert "astropy__astropy-14182" in parts[-1]
    assert "C" in parts[-1] and "M" in parts[-1]  # cell_label 片段
    assert parts[-1].endswith(".db")


def test_run_db_path_differs_by_repeat() -> None:
    a = _run_db_path("t1", _cell(repeat=0))
    b = _run_db_path("t1", _cell(repeat=1))
    assert a != b


def test_run_db_path_sanitizes_unsafe_chars() -> None:
    p = _run_db_path("bad/name:1", _cell())
    parts = _rel_parts(p)
    assert parts[0] == "eval"
    assert parts[-1] == "bad_name_1__C_sx_M_r2.db"


# ── P0-7: TaskResult 携带 run_id 与验收字段 ────────────────────────────

def test_taskresult_carries_run_id_and_verdict_fields() -> None:
    r = TaskResult(
        instance_id="x",
        cell=_cell(),
        passed=True,
        run_id="abc123",
        verified=True,
        f2p_pass=True,
        p2p_pass=True,
    )
    assert r.run_id == "abc123"
    assert r.verified is True
    assert r.f2p_pass is True and r.p2p_pass is True


def test_taskresult_defaults_verdict_fields() -> None:
    r = TaskResult(instance_id="x", cell=_cell(), passed=None)
    assert r.run_id == ""
    assert r.verified is None
    assert r.verdict_reason == ""


# ── #10: manifest 断点续跑 ───────────────────────────────────────────────

def test_cell_label_roundtrip() -> None:
    for cell in (EvalCell(True, True, True, 0), EvalCell(False, False, False, 3)):
        assert parse_cell_label(cell_label(cell)) == cell


def test_mark_done_then_load_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    m: dict = {}
    task = {"instance_id": "t1"}
    mark_done(m, task, _cell(repeat=0))
    assert m["t1__C_sx_M_r0"]["status"] == "done"
    assert load_manifest()["t1__C_sx_M_r0"]["status"] == "done"


def test_manifest_survives_rerun_skip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    m: dict = {}
    mark_done(m, {"instance_id": "t1"}, _cell(repeat=0))
    m2 = load_manifest()
    assert "t1__C_sx_M_r0" in m2
    assert "t2__C_sx_M_r0" not in m2


def test_failed_manifest_retries_then_skips(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    m: dict = {}
    mark_done(m, {"instance_id": "t1"}, _cell(repeat=0), error="checkout failed")
    assert m["t1__C_sx_M_r0"]["status"] == "failed"
    assert m["t1__C_sx_M_r0"]["retries"] == 1
    from eval.harness import _should_skip
    assert _should_skip(m, "t1__C_sx_M_r0") is False  # 瞬态失败允许重试
    mark_done(m, {"instance_id": "t1"}, _cell(repeat=0), error="checkout failed")
    assert m["t1__C_sx_M_r0"]["retries"] == 2
    assert _should_skip(m, "t1__C_sx_M_r0") is True   # 2 次后跳过
    mark_done(m, {"instance_id": "t2"}, _cell(repeat=0), error="env_broken", terminal=True)
    assert _should_skip(m, "t2__C_sx_M_r0") is True   # terminal 失败直接跳过
    assert _should_skip(m, "t3__C_sx_M_r0") is False  # 未跑过


def test_save_manifest_is_atomic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    save_manifest({"a": {"status": "done"}})
    assert load_manifest() == {"a": {"status": "done"}}
    # 无残留 tmp 文件
    assert not (tmp_path / "manifest.json.tmp").exists()


# ── #8/#d: deps 指纹 ─────────────────────────────────────────────────────

def test_venv_lock_sha1_nolock_when_missing(tmp_path: Path) -> None:
    assert _venv_lock_sha1({"repo": "a/b", "base_commit": "c" * 40}, tmp_path) == "nolock"


def test_venv_lock_sha1_changes_with_content(tmp_path: Path) -> None:
    from eval.env import venv_key

    key = venv_key({"repo": "a/b", "base_commit": "c" * 40})
    lock_dir = tmp_path / key
    lock_dir.mkdir(parents=True)
    (lock_dir / "requirements.lock").write_text("numpy==1.26\n", encoding="utf-8")
    h1 = _venv_lock_sha1({"repo": "a/b", "base_commit": "c" * 40}, tmp_path)
    (lock_dir / "requirements.lock").write_text("numpy==2.0\n", encoding="utf-8")
    h2 = _venv_lock_sha1({"repo": "a/b", "base_commit": "c" * 40}, tmp_path)
    assert h1 != h2


# ── #8: TaskResult 序列化（results 落盘 + --regen） ─────────────────────

def test_taskresult_roundtrip() -> None:
    r = TaskResult(
        instance_id="x", cell=_cell(repeat=1), passed=True, error=None,
        stats={"cost_usd": 0.1, "metrics": {"tool_total": 3}},
        trajectory_path="runs/eval/x.db", run_id="rid",
        verified=True, f2p_pass=True, p2p_pass=True, verdict_reason="ok",
    )
    d = r.to_dict()
    back = TaskResult.from_dict(d)
    assert back.instance_id == r.instance_id
    assert back.cell == r.cell
    assert back.verified is True
    assert back.stats == r.stats
    assert back.verdict_reason == "ok"


# ── #c: 成本估算 ─────────────────────────────────────────────────────────

def test_run_cost_scales_with_tokens() -> None:
    assert _run_cost({"total_input_tokens": 1_000_000, "total_output_tokens": 0}, 0.28, 1.10) == 0.28
    assert _run_cost({"total_input_tokens": 0, "total_output_tokens": 1_000_000}, 0.28, 1.10) == 1.10
    assert _run_cost({"total_input_tokens": 0, "total_output_tokens": 0}, 0.28, 1.10) == 0.0
    assert _run_cost({}, 0.28, 1.10) == 0.0


# ── P0-2b: 评测 prompt 适配（验证标准 + 无交互声明，不改 task 数据） ─────

def test_task_prompt_appends_verification_and_env_note() -> None:
    from eval.harness import _task_prompt

    task = {
        "instance_id": "x",
        "problem_statement": "Bug: foo is broken",
        "FAIL_TO_PASS": ["tests/test_foo.py::test_a"],
    }
    text = _task_prompt(task)
    assert text.startswith("Bug: foo is broken")
    assert "pytest tests/test_foo.py::test_a" in text
    assert "自动化评测，无交互通道" in text
    # task 数据未被修改
    assert task["problem_statement"] == "Bug: foo is broken"
    assert "自动化评测" not in task["problem_statement"]


def test_task_prompt_no_f2p_skips_verification() -> None:
    from eval.harness import _task_prompt

    text = _task_prompt({"problem_statement": "just explain"})
    assert "验证标准" not in text
    assert "自动化评测" in text


# ── #10+#8: 同一 db 多 run 时 stats 按 run_id 过滤 ───────────────────────

def _seed_db_with_two_runs(db_path: Path) -> None:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE events (run_id TEXT, turn INT, type TEXT, payload TEXT)")
    for rid in ("aaa", "bbb"):
        conn.execute("INSERT INTO runs VALUES (?)", (rid,))
    for rid in ("aaa", "bbb"):
        for i in range(10):
            conn.execute(
                "INSERT INTO events VALUES (?, ?, 'turn_start', '{}')", (rid, i)
            )
    conn.commit()
    conn.close()


def test_extract_stats_filters_by_run_id(tmp_path: Path) -> None:
    from eval.harness import _extract_stats

    db = tmp_path / "t.db"
    _seed_db_with_two_runs(db)
    both = _extract_stats(str(db))            # 不过滤：两个 run 事件合并
    one = _extract_stats(str(db), "aaa")      # 过滤：只看 aaa
    assert both["total_turns"] == 20
    assert one["total_turns"] == 10


# ── Supervision Agent（plans-0018）：stats 分列 + fake 冒烟 ───────────────

def _seed_db_with_supervision(db_path: Path) -> None:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE events (run_id TEXT, turn INT, type TEXT, payload TEXT)")
    conn.execute("INSERT INTO runs VALUES ('sup1')")
    usage = json.dumps({"input_tokens": 3000, "output_tokens": 100,
                        "cache_read_tokens": 2000})
    for i in range(3):
        conn.execute(
            "INSERT INTO events VALUES ('sup1', ?, 'supervision', ?)",
            (i * 6, json.dumps({"mode": "periodic", "assessment": "on_track",
                                "usage": json.loads(usage)})),
        )
    conn.execute(
        "INSERT INTO events VALUES ('sup1', 12, 'supervision', ?)",
        (json.dumps({"mode": "final", "assessment": "done",
                     "usage": json.loads(usage)}),),
    )
    conn.commit()
    conn.close()


def test_extract_stats_supervision_breakout(tmp_path: Path) -> None:
    from eval.harness import _extract_stats

    db = tmp_path / "s.db"
    _seed_db_with_supervision(db)
    stats = _extract_stats(str(db), "sup1")
    assert stats["supervision_calls"] == 4
    assert stats["supervision_input_tokens"] == 12000
    assert stats["supervision_output_tokens"] == 400
    assert stats["supervision_cache_read_tokens"] == 8000
    assert stats["run_end_reason"] == ""


def test_run_eval_fake_with_supervisor_smoke(tmp_path: Path, monkeypatch) -> None:
    """验收 2：--fake + 监督开不破坏冒烟（fake 返回非 JSON → 重试后跳过）。"""
    from eval.harness import run_eval

    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    task = {"instance_id": "fake__t1", "repo": "x/y", "base_commit": "a" * 40}
    cell = EvalCell(compression=True, concurrency=True, repo_map=True, repeat=0)
    results = run_eval(
        tasks=[task], matrix=[cell], workdir_base=str(tmp_path / "wd"),
        use_fake=True, max_turns=5, supervisor=True,
    )
    assert len(results) == 1
    stats = results[0].stats
    assert stats["supervision_calls"] == 2   # 解析失败重试 1 次后跳过
    assert stats["supervision_cost_usd"] >= 0
    assert "supervision" in stats.get("run_end_reason", "") or stats["run_end_reason"] in (
        "end_turn", "max_turns", "supervisor_done", "stagnant")


def test_run_eval_fake_without_supervisor_no_supervision_stats(tmp_path: Path, monkeypatch) -> None:
    from eval.harness import run_eval

    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    task = {"instance_id": "fake__t2", "repo": "x/y", "base_commit": "a" * 40}
    cell = EvalCell(compression=True, concurrency=True, repo_map=True, repeat=0)
    results = run_eval(
        tasks=[task], matrix=[cell], workdir_base=str(tmp_path / "wd"),
        use_fake=True, max_turns=5, supervisor=False,
    )
    assert results[0].stats["supervision_calls"] == 0


# ── repo 本地缓存：归档 → 恢复 roundtrip（网络免疫 + 480 runs 免重复 clone） ──

def _make_task(repo: str = "fake/repo", commit: str = "a" * 40,
               instance: str = "fake__t1") -> dict:
    return {"repo": repo, "base_commit": commit, "instance_id": instance}


def _init_git_repo(d: Path) -> None:
    import subprocess as sp
    d.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "init", "-q"], cwd=str(d), check=True)
    sp.run(["git", "config", "user.email", "t@x"], cwd=str(d), check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=str(d), check=True)
    (d / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    sp.run(["git", "add", "."], cwd=str(d), check=True)
    sp.run(["git", "commit", "-qm", "base"], cwd=str(d), check=True)


def test_repo_cache_archive_and_restore(tmp_path: Path, monkeypatch) -> None:
    import subprocess as sp
    from eval.harness import _archive_workdir, _set_workdir

    monkeypatch.setattr("eval.harness.REPO_CACHE", tmp_path / "cache")
    src = tmp_path / "src"
    _init_git_repo(src)
    commit = sp.run(["git", "-C", str(src), "rev-parse", "HEAD"],
                    capture_output=True, text=True).stdout.strip()
    task = _make_task(commit=commit)
    _archive_workdir(task, str(src))

    # 恢复目标目录不存在 → 缓存命中直接恢复（不 clone 不删源）
    workdir_base = tmp_path / "wd"
    restored = _set_workdir(task, str(workdir_base))
    assert Path(restored).exists()
    assert (Path(restored) / "app.py").exists()
    head = sp.run(["git", "-C", restored, "rev-parse", "--short", "HEAD"],
                  capture_output=True, text=True).stdout.strip()
    assert head == commit[:7]

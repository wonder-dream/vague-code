from __future__ import annotations

from pathlib import Path

from eval.harness import _run_db_path
from eval.matrix import EvalCell, TaskResult


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

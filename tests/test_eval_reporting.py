from __future__ import annotations

import json
from pathlib import Path

from eval import audit_tasks, reporter
from eval.matrix import EvalCell, TaskResult


def _cell(compression: bool = True, concurrency: bool = False,
          repo_map: bool = True, repeat: int = 0) -> EvalCell:
    return EvalCell(compression=compression, concurrency=concurrency,
                    repo_map=repo_map, repeat=repeat)


def _r(instance: str, cell: EvalCell, verified: bool | None,
       passed: bool | None = None, verdict: str = "ok") -> TaskResult:
    return TaskResult(instance_id=instance, cell=cell,
                      passed=verified if passed is None else passed,
                      verified=verified,
                      f2p_pass=verified, p2p_pass=verified,
                      verdict_reason=verdict,
                      stats={"total_turns": 3})


# ── P0-6: pass^k ─────────────────────────────────────────────────────────

def test_passk_counts_only_all_repeats_verified() -> None:
    results = [
        _r("A", _cell(repeat=0), True),
        _r("A", _cell(repeat=1), True),
        _r("B", _cell(repeat=0), True),
        _r("B", _cell(repeat=1), False),
    ]
    by_cell: dict[str, list[TaskResult]] = {"k": results}
    rows, total_num, total_den = reporter._passk(by_cell)
    key, k, num, den = rows[0]
    assert (k, num, den) == (2, 1, 2)
    assert (total_num, total_den) == (1, 2)


def test_passk_ignores_none_verified() -> None:
    results = [
        _r("A", _cell(repeat=0), None),
        _r("A", _cell(repeat=1), None),
    ]
    rows, total_num, total_den = reporter._passk({"k": results})
    assert rows[0][2] == 0 and rows[0][3] == 1


def test_generate_report_includes_passk_section(tmp_path: Path) -> None:
    results = [
        _r("A", _cell(repeat=0), True),
        _r("A", _cell(repeat=1), True),
        _r("B", _cell(repeat=0), False),
        _r("B", _cell(repeat=1), False),
    ]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    text = out.read_text(encoding="utf-8")
    assert "## pass^k 可靠性" in text
    assert "pass^k" in text


def test_generate_report_includes_failure_distribution(tmp_path: Path) -> None:
    results = [
        _r("A", _cell(repeat=0), True),
        _r("B", _cell(repeat=0), False, verdict="no_diff"),
        _r("C", _cell(repeat=0), False, verdict="f2p:fail"),
        _r("D", _cell(repeat=0), False, verdict="f2p:fail"),
    ]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    text = out.read_text(encoding="utf-8")
    assert "## 失败模式分布" in text
    assert "测试不过" in text or "伪完成" in text


# ── P0-5: audit_tasks ────────────────────────────────────────────────────

def test_audit_excludes_dirty_tasks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit_tasks, "load_sanity_cache", lambda: {})
    tasks = [
        {"instance_id": "a__1", "repo": "x/x"},
        {"instance_id": "a__2", "repo": "x/x"},
        {"instance_id": "a__3", "repo": "x/x"},
    ]
    scores_path = tmp_path / "scores.json"
    scores_path.write_text(json.dumps({
        "a__1": {"clarity": 0, "f2p_reach": 0},
        "a__2": {"clarity": 2, "f2p_reach": 1},
        "a__3": {"clarity": 0, "f2p_reach": 3},
    }), encoding="utf-8")
    out = tmp_path / "audit.md"
    audit_tasks.generate_report(tasks, scores_path, out)
    text = out.read_text(encoding="utf-8")
    assert "## 筛查判定标准" in text          # 判定标准写进报告开头
    assert "保留: 1 / 3" in text
    assert "剔除: 2" in text
    assert "`a__2`" in text and "`a__3`" in text


def test_audit_env_from_sanity_cache(tmp_path: Path, monkeypatch) -> None:
    from eval.env import venv_key
    task = {"instance_id": "a__1", "repo": "x/x", "base_commit": "abc123def456"}
    monkeypatch.setattr(audit_tasks, "load_sanity_cache",
                        lambda: {venv_key(task): False})
    scores_path = tmp_path / "s.json"
    scores_path.write_text(json.dumps({"a__1": {"clarity": 0, "f2p_reach": 0}}),
                           encoding="utf-8")
    out = tmp_path / "audit.md"
    audit_tasks.generate_report([task], scores_path, out)
    text = out.read_text(encoding="utf-8")
    assert "| broken |" in text
    assert "env broken" in text

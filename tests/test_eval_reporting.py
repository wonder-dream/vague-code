from __future__ import annotations

import json
from pathlib import Path

from eval import audit_tasks, reporter
from eval.matrix import EvalCell, TaskResult, cell_label


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
    # 全部 verified=None（env_broken）→ 不进分母，不惩罚通过率
    rows, total_num, total_den = reporter._passk({"k": results})
    assert rows == []
    assert (total_num, total_den) == (0, 0)


def test_passk_skips_env_broken_but_counts_mixed() -> None:
    # B 是 env_broken（全 None），A 是正常任务（1 过 1 挂）→ 分母只算 A
    results = [
        _r("A", _cell(repeat=0), True),
        _r("A", _cell(repeat=1), False),
        _r("B", _cell(repeat=0), None),
        _r("B", _cell(repeat=1), None),
    ]
    rows, total_num, total_den = reporter._passk({"k": results})
    assert rows[0][2] == 0 and rows[0][3] == 1
    assert (total_num, total_den) == (0, 1)


# ── OFAT 设计（消融成本腰斩：4 配置替代全因子 8 配置） ───────────────────

def test_build_matrix_ofat_has_4_configs() -> None:
    from eval.matrix import build_matrix
    cells = build_matrix(repeat=3, design="ofat")
    # 全开 k=3 + 3 个单变量关闭 k=3 = 12 cells
    assert len(cells) == 12
    labels = {cell_label(c) for c in cells}
    assert "C_X_M_r0" in labels and "nc_X_M_r0" in labels
    assert "C_sx_M_r0" in labels and "C_X_nm_r0" in labels
    # 全开 3 次、其余各 3 次
    from collections import Counter
    counts = Counter((c.compression, c.concurrency, c.repo_map) for c in cells)
    assert counts[(True, True, True)] == 3
    assert counts[(False, True, True)] == 3


def test_build_matrix_ofat_ablation_repeat() -> None:
    from eval.matrix import build_matrix
    cells = build_matrix(repeat=3, design="ofat", ablation_repeat=2)
    from collections import Counter
    counts = Counter((c.compression, c.concurrency, c.repo_map) for c in cells)
    assert counts[(True, True, True)] == 3    # 核心层 k=3
    assert counts[(False, True, True)] == 2   # 消融层 k=2
    assert len(cells) == 3 + 2 * 3


def test_build_matrix_full_design_keeps_8_configs() -> None:
    from eval.matrix import build_matrix
    cells = build_matrix(repeat=2, design="full")
    assert len(cells) == 16
    # 8 种配置 × 2 重复（label 含 repeat 后缀，配置粒度按去 repeat 前缀数）
    configs = {cell_label(c).rsplit("_r", 1)[0] for c in cells}
    assert len(configs) == 8

def test_head_to_head_lists_gain_and_loss_tasks(tmp_path: Path) -> None:
    # 固定 concurrency/repo_map，compression on 过而 off 不过的任务应列进 gain
    results = [
        # C on: A 过、B 不过
        _r("A", _cell(compression=True, concurrency=False, repo_map=True, repeat=0), True),
        _r("A", _cell(compression=True, concurrency=False, repo_map=True, repeat=1), True),
        _r("B", _cell(compression=True, concurrency=False, repo_map=True, repeat=0), False),
        # C off: A 不过、B 过
        _r("A", _cell(compression=False, concurrency=False, repo_map=True, repeat=0), False),
        _r("B", _cell(compression=False, concurrency=False, repo_map=True, repeat=0), True),
    ]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    text = out.read_text(encoding="utf-8")
    assert "## 逐题胜负表" in text
    assert "compression 开 vs 关" in text
    # A: 开过关不过 → gain；B: 关过开不过 → loss
    assert "开过/关不过: 1 题 → A" in text
    assert "关过/开不过: 1 题 → B" in text


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
    assert "## 失败分类分账" in text
    assert "改错" in text or "伪完成" in text


def test_generate_report_includes_metric_gauges(tmp_path: Path) -> None:
    """ADR-0040 双指标口径：pass@1 与 e2e mean 分列 + 声明模板。"""
    results = [
        _r("A", _cell(repeat=0), True),
        _r("B", _cell(repeat=0), False, verdict="f2p:fail"),
    ]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    text = out.read_text(encoding="utf-8")
    assert "## 指标口径" in text
    assert "pass@1" in text and "e2e mean" in text
    assert "50.00%" in text
    assert "## 声明" in text
    assert "不能与任何官方 leaderboard 分数对比" in text


def test_generate_report_includes_cost_percentiles(tmp_path: Path) -> None:
    """ADR-0040：成本与 token 分位段（p50/p90/max，报告 3.2-4 缺口）。"""
    def _cost_r(instance: str, tokens: int, cost: float):
        r = _r(instance, _cell(repeat=0), True)
        r.stats["total_input_tokens"] = tokens
        r.stats["total_output_tokens"] = tokens // 10
        r.stats["cache_read_tokens"] = tokens // 2
        r.stats["cost_usd"] = cost
        return r

    results = [_cost_r("A", 1000, 0.01), _cost_r("B", 2000, 0.02), _cost_r("C", 3000, 0.03)]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    text = out.read_text(encoding="utf-8")
    assert "## 成本与 token 统计" in text
    assert "cache-hit tokens" in text
    assert "| 2,000" in text  # p50 input tokens
    assert "0.0200" in text  # p50 cost


def test_generate_report_includes_pass_at_k(tmp_path: Path) -> None:
    """ADR-0040：pass@k（≥1 次过）与 pass^k（全过）分列。"""
    # A 两重复均过 → pass^k ✓ pass@k ✓；B 一过一不过 → pass^k ✗ pass@k ✓
    results = [
        _r("A", _cell(repeat=0), True),
        _r("A", _cell(repeat=1), True),
        _r("B", _cell(repeat=0), False, verdict="f2p:fail"),
        _r("B", _cell(repeat=1), True),
    ]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    text = out.read_text(encoding="utf-8")
    assert "pass@k（≥1 次过，Aider 口径）" in text
    assert "50%" in text  # pass^k = 1/2
    assert "100%（2/2）" in text  # pass@k = 2/2


def test_generate_report_includes_supervision_section(tmp_path: Path) -> None:
    def _sup_r(instance: str, calls: int, cost: float) -> TaskResult:
        return TaskResult(
            instance_id=instance, cell=_cell(), passed=None, verified=None,
            stats={
                "total_turns": 3,
                "supervision_calls": calls,
                "supervision_cost_usd": cost,
                "cost_usd": 1.0,
                "metrics": {"supervision_assessments": {"on_track": 2, "done": 1}},
            },
        )

    out = tmp_path / "report.md"
    reporter.generate_report(
        [_sup_r("A", 3, 0.05), _sup_r("B", 3, 0.05)], str(out),
    )
    text = out.read_text(encoding="utf-8")
    assert "## 监督质量" in text
    assert "监督调用/run" in text
    assert "on_track:4" in text and "done:2" in text  # 两 run 聚合
    assert "5.0%" in text  # 0.10 / 2.00 成本占比


def test_generate_report_no_supervision_section_when_absent(tmp_path: Path) -> None:
    results = [_r("A", _cell(repeat=0), True)]
    out = tmp_path / "report.md"
    reporter.generate_report(results, str(out))
    assert "## 监督质量" not in out.read_text(encoding="utf-8")


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

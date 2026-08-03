from __future__ import annotations

from eval.classify import CLASS_LABELS, classify, render_chart, write_chart
from eval.matrix import EvalCell, TaskResult


def _r(verified: bool | None, reason: str = "", run_end: str = "end_turn",
       touches: int = 0, denies: int = 0, error: str = "") -> TaskResult:
    return TaskResult(
        instance_id="t1", cell=EvalCell(True, False, True, 0),
        passed=verified, verified=verified, verdict_reason=reason,
        error=error,
        stats={"run_end_reason": run_end, "touches_test_files": touches,
               "metrics": {"permission_denies": denies}},
    )


def test_success() -> None:
    assert classify(_r(True)) == "success"


def test_no_diff() -> None:
    assert classify(_r(False, reason="no_diff")) == "no_diff"


def test_f2p_fail() -> None:
    assert classify(_r(False, reason="f2p:fail")) == "test_fail"


def test_p2p_fail() -> None:
    assert classify(_r(False, reason="p2p:fail")) == "test_fail"


def test_timeout() -> None:
    assert classify(_r(False, run_end="max_turns")) == "timeout"
    assert classify(_r(False, run_end="pending")) == "timeout"
    # max_turns 且无 diff → timeout（而非 no_diff）：撞墙没产出是超时
    assert classify(_r(False, reason="no_diff", run_end="max_turns")) == "timeout"


def test_gaming_tests_priority() -> None:
    # 钻空子优先于 test_fail
    assert classify(_r(False, reason="f2p:fail", touches=1)) == "gaming_tests"


def test_permission_blocked() -> None:
    assert classify(_r(False, reason="f2p:fail", denies=2)) == "permission_blocked"


def test_misunderstood_fallback() -> None:
    assert classify(_r(False, reason="end_turn")) == "misunderstood"


def test_injection_pierced_flag() -> None:
    r = _r(True)
    assert classify(r, injected=True) == "injection_pierced"


def test_env_error_classified_as_test_fail() -> None:
    assert classify(_r(False, error="sanity gate: F2P expected assertion-fail")) == "test_fail"


def test_distribution_and_chart() -> None:
    results = [_r(True), _r(False, reason="no_diff"),
               _r(False, reason="f2p:fail"), _r(False, reason="no_diff")]
    dist = {k: v for k, v in __import__("eval.classify", fromlist=["distribution"]).distribution(results).items()}
    assert dist["no_diff"] == 2
    lines = render_chart(results)
    assert any(CLASS_LABELS["no_diff"] in line for line in lines)
    assert "总 runs: 4" in lines


def test_write_chart(tmp_path) -> None:
    results = [_r(True), _r(False, reason="no_diff")]
    out = tmp_path / "dist.md"
    write_chart(results, out)
    assert "失败模式分布" in out.read_text(encoding="utf-8")

from __future__ import annotations

from eval.classify import CLASS_LABELS, classify
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
    assert classify(_r(False, reason="f2p:fail")) == "f2p_fail"


def test_p2p_fail() -> None:
    assert classify(_r(False, reason="p2p:fail")) == "p2p_fail"


def test_timeout() -> None:
    assert classify(_r(False, run_end="max_turns")) == "timeout"
    assert classify(_r(False, run_end="pending")) == "timeout"
    # max_turns 且无 diff → timeout（而非 no_diff）：撞墙没产出是超时
    assert classify(_r(False, reason="no_diff", run_end="max_turns")) == "timeout"


def test_gaming_tests_priority() -> None:
    # 钻空子优先于 f2p_fail
    assert classify(_r(False, reason="f2p:fail", touches=1)) == "gaming_tests"


def test_permission_blocked() -> None:
    assert classify(_r(False, reason="f2p:fail", denies=2)) == "permission_blocked"


def test_misunderstood_fallback() -> None:
    assert classify(_r(False, reason="end_turn")) == "misunderstood"


def test_injection_pierced_flag() -> None:
    r = _r(True)
    assert classify(r, injected=True) == "injection_pierced"


def test_env_error_classified_as_env_broken() -> None:
    """sanity gate 失败 = 环境问题（确定性剔除，不进能力分母，报告 4.3）。"""
    assert classify(_r(False, error="sanity gate: F2P expected assertion-fail")) == "env_broken"
    assert classify(_r(False, error="env_broken")) == "env_broken"


def test_infra_errors_classified_separately() -> None:
    """checkout/venv/网络/reward 缺失 = 基础设施错误，与模型失败分账。"""
    assert classify(_r(False, error="checkout failed: network")) == "infra"
    assert classify(_r(False, error="venv setup error")) == "infra"
    assert classify(_r(False, error="HTTP 429 rate limited")) == "infra"


def test_no_verdict_is_infra() -> None:
    """verified=None 且无 error（reward 文件缺失/verify 未产出）→ infra。"""
    assert classify(_r(None)) == "infra"


def test_verify_fail_mapped_to_f2p() -> None:
    """polyglot 容器 verifier 失败（ADR-0040）：实现/编译错 → f2p_fail；超时 → infra。"""
    assert classify(_r(False, reason="verify:fail(exit 127)")) == "f2p_fail"
    assert classify(_r(False, reason="verify:fail(exit 2)")) == "f2p_fail"
    assert classify(_r(False, reason="verify:timeout")) == "infra"


def test_stagnant_class() -> None:
    assert classify(_r(False, run_end="stagnant")) == "stagnant"
    # 停滞优先于 timeout 判断
    assert classify(_r(False, reason="no_diff", run_end="stagnant")) == "stagnant"


def test_supervisor_done_with_verified_false_is_failure() -> None:
    assert classify(_r(False, reason="f2p:fail", run_end="supervisor_done")) == "f2p_fail"
    assert classify(_r(False, reason="no_diff", run_end="supervisor_done")) == "no_diff"


def test_supervisor_done_with_verified_true_is_success() -> None:
    assert classify(_r(True, run_end="supervisor_done")) == "success"


def test_class_labels_are_exhaustive_singleton_keys() -> None:
    """分类标签唯一且含全部类。"""
    assert len(CLASS_LABELS) == len(set(CLASS_LABELS))
    for cls in ("success", "f2p_fail", "p2p_fail", "no_diff", "gaming_tests",
                "timeout", "env_broken", "infra", "permission_blocked",
                "injection_pierced", "stagnant", "misunderstood"):
        assert cls in CLASS_LABELS

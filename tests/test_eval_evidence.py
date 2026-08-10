"""证据链三件套 config/lock/result + 定向恢复测试（ADR-0040，报告 4.5）。"""

from __future__ import annotations

import json

from eval.evidence import _task_digest, write_evidence
from eval.matrix import EvalCell, TaskResult


def _task(instance_id: str = "t1") -> dict:
    return {
        "instance_id": instance_id,
        "problem_statement": "Fix the bug",
        "test_patch": "diff --git a/test_t.py b/test_t.py\n+def test_x(): pass",
        "FAIL_TO_PASS": ["test_t.py::test_x"],
        "PASS_TO_PASS": [],
        "base_commit": "abc123",
    }


def _result(instance_id: str, verified: bool | None, reason: str = "") -> TaskResult:
    return TaskResult(
        instance_id=instance_id, cell=EvalCell(True, True, True, 0),
        passed=verified, verified=verified, verdict_reason=reason,
        stats={"cost_usd": 0.01, "total_turns": 3},
    )


def test_task_digest_is_stable_and_content_sensitive() -> None:
    t1 = _task()
    t2 = _task()
    t3 = _task()
    t3["test_patch"] = t3["test_patch"] + "\n+def test_y(): pass"
    assert _task_digest(t1) == _task_digest(t2)
    assert _task_digest(t1) != _task_digest(t3)
    assert len(_task_digest(t1)) == 16


def test_write_evidence_creates_triple(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("eval.evidence._version", lambda: "test-version")
    monkeypatch.setattr("eval.evidence._deps_fingerprint", lambda tasks: {})
    tasks = [_task("t1"), _task("t2")]
    results = [_result("t1", True), _result("t2", False, reason="f2p:fail")]
    out = write_evidence(tmp_path / "run", {"model": "m", "args": {"repeat": 3}}, tasks, results)

    config = json.loads((out / "config.json").read_text(encoding="utf-8"))
    assert config["model"] == "m"

    lock = json.loads((out / "lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == "test-version"
    assert lock["task_count"] == 2
    assert lock["run_count"] == 2
    assert set(lock["tasks"]) == {"t1", "t2"}
    assert len(lock["tasks"]["t1"]) == 16

    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert len(result) == 2
    assert result[0]["verified"] is True
    assert result[1]["verdict_reason"] == "f2p:fail"


def test_clear_manifest_by_class(tmp_path, monkeypatch) -> None:
    """定向恢复：按分类清除 manifest 条目，无关分类保留。"""
    import eval.cli as cli_mod
    from eval.harness import load_manifest, save_manifest

    monkeypatch.setattr("eval.harness.MANIFEST_PATH", tmp_path / "manifest.json")
    manifest = {
        "t1__C_X_M_r0": {"status": "done", "error": None},
        "t2__C_X_M_r0": {"status": "done", "error": "infra", "retries": 1},
        "t3__C_X_M_r0": {"status": "done", "error": None},
    }
    save_manifest(manifest)
    results = [
        _result("t1", True),
        _result("t2", None, reason=""),   # verified=None → infra
        _result("t3", False, reason="no_diff"),
    ]
    monkeypatch.setattr(cli_mod, "_latest_results", lambda *a, **k: results)
    from eval.harness import MANIFEST_PATH
    assert MANIFEST_PATH.exists()

    removed = cli_mod._clear_manifest_by_class("infra")
    assert removed == 1
    after = load_manifest()
    assert "t2__C_X_M_r0" not in after
    assert "t1__C_X_M_r0" in after
    assert "t3__C_X_M_r0" in after

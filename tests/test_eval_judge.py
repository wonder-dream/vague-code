from __future__ import annotations

import json
from pathlib import Path

from vague_code.agent.config import AgentConfig
from vague_code.agent.ir import Message, ModelResponse, NormalizedUsage, StopReason, TextBlock
from vague_code.agent.trajectory import EventType, Trajectory

from eval.judge import (
    RunRecord,
    build_messages,
    judge_run,
    parse_judge_output,
    sample_audit,
)
from eval.rubric import get_rubric


def _persist_minimal_run(tmp: Path) -> tuple[str, str]:
    """造一个最小轨迹 db，返回 (run_id, db_path)。"""
    config = AgentConfig(db_path=str(tmp / "run.db"))
    traj = Trajectory(run_id="r_deadbeef", config=config)
    traj.emit(EventType.run_start, payload={
        "task": "Fix the bug", "workdir": str(tmp),
        "system_prompt": "sys",
    })
    traj.emit(EventType.llm_response, payload={"blocks": [
        {"type": "text", "text": "ok, fixed"}
    ]})
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    traj.persist()
    return traj.run_id, str(tmp / "run.db")


class _FakeJudgeBackend:
    def __init__(self, raw: str):
        self._raw = raw

    def complete(self, messages, tools=None, config=None):
        return ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text=self._raw)]),
            stop_reason=StopReason.end_turn,
            usage=NormalizedUsage(input_tokens=10, output_tokens=5),
        )


# ── parse_judge_output ───────────────────────────────────────────────────

def test_parse_valid_output() -> None:
    raw = '{"dimensions": {"final_correctness": 4, "root_cause": 3, "diff_quality": 5, "efficiency": 2}, "verdict": "pass", "justification": "修好了"}'
    parsed = parse_judge_output(raw, ["final_correctness", "root_cause", "diff_quality", "efficiency"])
    assert parsed is not None
    assert parsed["scores"]["final_correctness"] == 4
    assert parsed["verdict"] == "pass"


def test_parse_fenced_json() -> None:
    raw = "```json\n{\"dimensions\": {\"final_correctness\": 3}, \"verdict\": \"partial\", \"justification\": \"x\"}\n```"
    parsed = parse_judge_output(raw, ["final_correctness"])
    assert parsed and parsed["verdict"] == "partial"


def test_parse_bad_verdict_rejected() -> None:
    raw = '{"dimensions": {"final_correctness": 3}, "verdict": "maybe", "justification": ""}'
    assert parse_judge_output(raw, ["final_correctness"]) is None


def test_parse_non_json_rejected() -> None:
    assert parse_judge_output("i think it passed", ["final_correctness"]) is None


# ── judge_run（含重试） ─────────────────────────────────────────────────

def test_judge_run_parses_result(tmp_path: Path) -> None:
    run_id, db = _persist_minimal_run(tmp_path)
    record = RunRecord(instance_id="t1", run_id=run_id, db_path=db,
                       task="Fix the bug", workdir=str(tmp_path))
    raw = json.dumps({
        "dimensions": {"final_correctness": 5, "root_cause": 4,
                       "diff_quality": 5, "efficiency": 3},
        "verdict": "pass", "justification": "good",
    })
    result = judge_run(_FakeJudgeBackend(raw), record, get_rubric("fix_bug"))
    assert result.error is None
    assert result.scores["final_correctness"] == 5
    assert result.verdict == "pass"


def test_judge_run_unparsable_after_retries(tmp_path: Path) -> None:
    run_id, db = _persist_minimal_run(tmp_path)
    record = RunRecord(instance_id="t1", run_id=run_id, db_path=db,
                       task="Fix the bug", workdir=str(tmp_path))
    result = judge_run(_FakeJudgeBackend("nope not json"), record, get_rubric("fix_bug"))
    assert result.error == "unparsable_judge_output"


# ── build_messages（P0-7 独立 db 喂 judge） ─────────────────────────────

def test_build_messages_includes_task_verdict_and_schema(tmp_path: Path) -> None:
    run_id, db = _persist_minimal_run(tmp_path)
    record = RunRecord(instance_id="t1", run_id=run_id, db_path=db,
                       task="Fix the bug", workdir=str(tmp_path),
                       verified=True, verdict_reason="ok")
    messages = build_messages(record, get_rubric("fix_bug"), db)
    assert messages[0].role == "system"
    assert "JSON" in messages[0].content[0].text
    user_text = "".join(b.text for b in messages[1].content)
    assert "Fix the bug" in user_text
    assert "验收信号" in user_text
    assert "评分量表" in messages[0].content[0].text


# ── 人工一致性审计抽样（故意混一半 verified=False） ─────────────────────

def test_sample_audit_balances_verified() -> None:
    records = [
        RunRecord(instance_id=f"p{i}", run_id=f"pr{i}", db_path="", task="",
                  verified=True) for i in range(10)
    ]
    records += [
        RunRecord(instance_id=f"f{i}", run_id=f"fr{i}", db_path="", task="",
                  verified=False) for i in range(10)
    ]
    chosen = sample_audit(records, n=8)
    verified_true = sum(1 for r in chosen if r.verified is True)
    verified_false = sum(1 for r in chosen if r.verified is False)
    assert len(chosen) == 8
    assert verified_true == 4 and verified_false == 4

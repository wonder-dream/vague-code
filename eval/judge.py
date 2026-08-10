from __future__ import annotations

import argparse
import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from eval.rubric import JUDGE_SYSTEM_PREFIX, Rubric, get_rubric


# ── run 记录（离线判题的定位基础，P0-7 的独立 db 由此消费） ────────────

@dataclass
class RunRecord:
    instance_id: str
    run_id: str
    db_path: str
    task: str
    workdir: str = ""
    verified: bool | None = None
    verdict_reason: str = ""


@dataclass
class JudgeResult:
    instance_id: str
    run_id: str
    scores: dict[str, int]
    verdict: str
    justification: str
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "run_id": self.run_id,
            "scores": self.scores,
            "verdict": self.verdict,
            "justification": self.justification,
            "error": self.error,
        }


# ── 加载 run ────────────────────────────────────────────────────────────

def load_manifest(path: str | Path) -> list[RunRecord]:
    records: list[RunRecord] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        records.append(RunRecord(
            instance_id=d.get("instance_id", ""),
            run_id=d.get("run_id", ""),
            db_path=d.get("db_path", ""),
            task=d.get("task", ""),
            workdir=d.get("workdir", ""),
            verified=d.get("verified"),
            verdict_reason=d.get("verdict_reason", ""),
        ))
    return records


def scan_runs(runs_dir: str | Path) -> list[RunRecord]:
    """回退路径：扫描 runs/eval/*.db，从 run_start 事件恢复 task/workdir。"""
    from src.agent.trajectory import Trajectory

    records: list[RunRecord] = []
    for db in sorted(Path(runs_dir).glob("*.db")):
        try:
            conn_sqlite = __import__("sqlite3").connect(str(db))
            row = conn_sqlite.execute(
                "SELECT run_id FROM runs ORDER BY rowid DESC LIMIT 1"
            ).fetchall()
            conn_sqlite.close()
        except Exception:
            continue
        for (run_id,) in row:
            try:
                traj = Trajectory.from_db(run_id, str(db))
            except Exception:
                continue
            start = next((e for e in traj.events if e.type.value == "run_start"), None)
            if start is None:
                continue
            payload = start.payload
            records.append(RunRecord(
                instance_id=payload.get("instance_id", run_id),
                run_id=run_id,
                db_path=str(db),
                task=payload.get("task", ""),
                workdir=payload.get("workdir", ""),
            ))
    return records


# ── prompt 构建 ─────────────────────────────────────────────────────────

MAX_TOOL_CHARS = 600
MAX_TRANSCRIPT_CHARS = 12_000


def _transcript_text(record: RunRecord, db_path: str) -> str:
    from src.agent.trajectory import Trajectory

    traj = Trajectory.from_db(record.run_id, db_path)
    messages = traj.to_messages()
    lines: list[str] = []
    total = 0
    for msg in messages:
        parts: list[str] = []
        for b in msg.content:
            t = b._type() if hasattr(b, "_type") else "block"
            if t == "text":
                parts.append(b.text)
            elif t == "tool_use":
                parts.append(f"[tool_call] {b.name}({json.dumps(b.input, ensure_ascii=False)[:300]})")
            elif t == "tool_result":
                content = b.content
                if len(content) > MAX_TOOL_CHARS:
                    content = content[:MAX_TOOL_CHARS] + f"...[truncated {len(b.content)} chars]"
                parts.append(f"[tool_result{'(!error)' if b.is_error else ''}] {content}")
        text = "\n".join(parts)
        if not text.strip():
            continue
        total += len(text)
        if total > MAX_TRANSCRIPT_CHARS:
            lines.append("...[transcript truncated]")
            break
        lines.append(f"--- {msg.role} ---\n{text}")
    return "\n".join(lines)


def _git_diff(workdir: str) -> str:
    if not workdir or not Path(workdir).exists() or not (Path(workdir) / ".git").exists():
        return ""
    import subprocess

    proc = subprocess.run(
        ["git", "-C", workdir, "diff", "HEAD"], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout[:4000]


def build_messages(record: RunRecord, rubric: Rubric, db_path: str) -> list:
    from src.agent.ir import Message

    system = "\n\n".join([
        JUDGE_SYSTEM_PREFIX,
        "## 评分量表（锚定示例）",
        rubric.to_prompt(),
        "verdict 判定：pass=任务完全解决；partial=方向对但未完成；fail=未解决或方向错误。",
    ])

    sections: list[str] = [f"## 任务\n{record.task or '(无任务文本)'}"]
    if record.verified is not None:
        sections.append(
            f"## 验收信号（P0 真结果，仅供参考）\n"
            f"F2P/P2P 验证: {'通过' if record.verified else '未通过'} "
            f"(reason: {record.verdict_reason or '-'})"
        )
    diff = _git_diff(record.workdir)
    if diff.strip():
        sections.append(f"## 最终 diff\n```diff\n{diff}\n```")
    sections.append(f"## Agent 轨迹\n{_transcript_text(record, db_path)}")
    sections.append("只输出 JSON，不要输出其他文字。")

    return [
        Message(role="system", content=system),
        Message(role="user", content="\n\n".join(sections)),
    ]


# ── 输出解析（鲁棒，离线可重 parse） ───────────────────────────────────

def _extract_json(raw: str) -> dict | None:
    for m in re.finditer(r"\{.*\}", raw, re.DOTALL):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            continue
    return None


def parse_judge_output(raw: str, dimensions: list[str]) -> dict | None:
    data = _extract_json(raw)
    if not data:
        return None
    dims = data.get("dimensions")
    if not isinstance(dims, dict):
        return None
    scores: dict[str, int] = {}
    for name in dimensions:
        v = dims.get(name)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        scores[name] = int(round(v))
    verdict = data.get("verdict")
    if verdict not in ("pass", "partial", "fail"):
        return None
    return {
        "scores": scores,
        "verdict": verdict,
        "justification": str(data.get("justification", "")),
    }


# ── judge 单条 / 批量 ───────────────────────────────────────────────────

def judge_run(
    backend: Any,
    record: RunRecord,
    rubric: Rubric,
    db_path: str | None = None,
    retries: int = 2,
) -> JudgeResult:
    db_path = db_path or record.db_path
    messages = build_messages(record, rubric, db_path)
    dims = [d.name for d in rubric.dimensions]
    config = {"temperature": 0}
    judge_model = getattr(backend, "_judge_model", None)
    if judge_model:
        config["model"] = judge_model
    last_raw = ""
    for _ in range(retries):
        resp = backend.complete(messages, config=config)
        last_raw = "".join(b.text for b in resp.message.content if hasattr(b, "text"))
        parsed = parse_judge_output(last_raw, dims)
        if parsed:
            return JudgeResult(
                instance_id=record.instance_id, run_id=record.run_id,
                scores=parsed["scores"], verdict=parsed["verdict"],
                justification=parsed["justification"],
            )
    return JudgeResult(
        instance_id=record.instance_id, run_id=record.run_id,
        scores={}, verdict="fail", justification="",
        error="unparsable_judge_output",
    )


def _build_backend(judge_model: str) -> Any:
    from src.agent.backend import create_deepseek_backend

    key = (dotenv_values().get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set (set it in .env or environment)")
    backend = create_deepseek_backend(api_key=key, base_url="https://api.deepseek.com", timeout_s=120.0)
    backend._judge_model = judge_model  # type: ignore[attr-defined]
    return backend


# ── 人工一致性审计（P1：故意混入一半 verified=False） ─────────────────

def sample_audit(records: list[RunRecord], n: int = 20, seed: int = 42) -> list[RunRecord]:
    if not n:
        return []
    rng = random.Random(seed)
    passed = [r for r in records if r.verified is True]
    failed = [r for r in records if r.verified is False]
    half = n // 2
    chosen = rng.sample(passed, min(half, len(passed)))
    chosen += rng.sample(failed, min(n - half, len(failed)))
    # 不足一半时用其他记录补齐（verified=None 优先算 failed 侧）
    rest = [r for r in records if r not in chosen]
    while len(chosen) < n and rest:
        chosen.append(rest.pop(0))
    return chosen[:n]


def write_audit_samples(records: list[RunRecord], path: str | Path, n: int = 20) -> list[RunRecord]:
    chosen = sample_audit(records, n)
    Path(path).write_text(json.dumps(
        [{"instance_id": r.instance_id, "run_id": r.run_id,
          "verified": r.verified, "db_path": r.db_path,
          "human_final_correctness": None} for r in chosen],
        indent=2, ensure_ascii=False,
    ), encoding="utf-8")
    print(f"Audit samples ({len(chosen)} records, ~half verified=False) written to {path}")
    return chosen


def consistency(
    judge_results: list[JudgeResult],
    human_scores_path: str | Path,
) -> dict[str, Any]:
    human = json.loads(Path(human_scores_path).read_text(encoding="utf-8"))
    human_by_run = {h["run_id"]: h for h in human if h.get("human_final_correctness") is not None}
    judged_by_run = {r.run_id: r for r in judge_results}

    pairs = [(judged_by_run[k], h) for k, h in human_by_run.items() if k in judged_by_run]
    if not pairs:
        return {"error": "no overlapping judged+human-scored records"}

    exact = sum(1 for j, h in pairs if j.scores.get("final_correctness") == h["human_final_correctness"])
    within1 = sum(
        1 for j, h in pairs
        if abs(j.scores.get("final_correctness", 0) - h["human_final_correctness"]) <= 1
    )
    n = len(pairs)
    return {
        "n": n,
        "exact_agreement": round(exact / n, 3),
        "within_1_agreement": round(within1 / n, 3),
    }


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="P1 LLM-as-Judge（离线，改提示词只重评分）")
    p.add_argument("--manifest", help="run manifest jsonl")
    p.add_argument("--runs", default="runs/eval", help="扫描目录（回退）")
    p.add_argument("--judge-model", default="deepseek-v4-flash",
                   help="judge 模型，独立于被评 Agent 模型（自我增强偏差缓解）")
    p.add_argument("--rubric", default="fix_bug", choices=["fix_bug", "explain"])
    p.add_argument("--out", default="eval/judge_results.jsonl")
    p.add_argument("--audit", type=int, default=0,
                   help="抽样 N 条人工审计样本（一半 verified=False）")
    p.add_argument("--audit-out", default="eval/judge_audit_samples.json")
    p.add_argument("--consistency", help="用人工打分文件计算一致性")
    p.add_argument("--limit", type=int, default=0,
                   help="只抽评 N 条（同样混一半 verified=False，省成本）")
    args = p.parse_args()

    records = load_manifest(args.manifest) if args.manifest else scan_runs(args.runs)
    if not records:
        print("No runs found. Pass --manifest or point --runs at trajectory dbs.")
        return

    if args.consistency:
        judge_results = _load_judge_results(args.out)
        print(consistency(judge_results, args.consistency))
        return

    if args.audit:
        write_audit_samples(records, args.audit_out, args.audit)
        return

    rubric = get_rubric(args.rubric)
    if args.limit:
        records = sample_audit(records, args.limit)
        print(f"Sampled {len(records)} runs for judging "
              f"({sum(1 for r in records if r.verified is True)} verified / "
              f"{sum(1 for r in records if r.verified is False)} failed)")
    backend = _build_backend(args.judge_model)
    results = [judge_run(backend, r, rubric) for r in records]

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    verified = [r for r in results if not r.error]
    if verified:
        by_dim: dict[str, list[int]] = {}
        for r in verified:
            for k, v in r.scores.items():
                by_dim.setdefault(k, []).append(v)
        print(f"Judged {len(results)} runs -> {args.out}")
        for k, vs in by_dim.items():
            print(f"  {k}: avg={sum(vs) / len(vs):.2f} (n={len(vs)})")


def _load_judge_results(path: str | Path) -> list[JudgeResult]:
    out: list[JudgeResult] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out.append(JudgeResult(
            instance_id=d["instance_id"], run_id=d["run_id"], scores=d["scores"],
            verdict=d["verdict"], justification=d["justification"],
            error=d.get("error"),
        ))
    return out


if __name__ == "__main__":
    main()

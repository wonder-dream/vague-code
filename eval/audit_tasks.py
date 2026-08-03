from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from eval.env import venv_key
from eval.verify import load_sanity_cache

OFFICIAL_CSV = Path(__file__).parent / "official_annotations.csv"


def load_official() -> dict[str, dict[str, Any]]:
    """OpenAI SWE-bench Verified 人工标注（问题清晰度 / 测试有效性 / 剔除判定）。"""
    if not OFFICIAL_CSV.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in csv.DictReader(OFFICIAL_CSV.open(encoding="utf-8")):
        out[r["instance_id"]] = {
            "clarity": int(float(r.get("underspecified", 0) or 0)),
            "f2p_reach": int(float(r.get("false_negative", 0) or 0)),
            "difficulty": r.get("difficulty", ""),
            "filter_out": (r.get("filter_out", "false") or "").lower() == "true",
        }
    return out

# ── 判定标准定义（照 SWE-bench Verified 标注法，写进 audit_results.md 开头） ──

CRITERIA_TEXT = """## 筛查判定标准（单遍人工筛查，三维度各 0-3）

**维度 1 · 问题清晰度（clarity）**
- 0 = 无需追问即可开工，issue 明确描述了预期行为
- 1 = 有少量留白，但存在合理解释（可从 issue 复现代码推断）
- 2 = 有歧义，需在多个合理方案间判断（如"是否删 API 是意图还是 bug"）
- 3 = 几乎无法理解要求做什么

**维度 2 · F2P 可达性（f2p_reach）**
- 0 = 测试完美覆盖所有合理解
- 1 = 覆盖多数合理解，个别非常规解会被漏判
- 2 = 会误杀一些合理实现（断言过严 / 与 issue 关联弱）
- 3 = 测试与 issue 无关，或可被钻空子（改测试文件即过）

**维度 3 · 环境可搭性（env，自动来自 sanity gate 双检）**
- passed = venv 可搭，F2P 在干净 checkout 上断言失败、P2P 通过
- broken = sanity gate 任一检失败（判别器失效或环境装错）
- not_curated = eval/env.py 尚无该 repo 的 install 规格（待策展）
"""

EXCLUDE_RULES = [
    ("clarity >= 2", lambda s: (s.get("clarity") or 0) >= 2),
    ("f2p_reach >= 2", lambda s: (s.get("f2p_reach") or 0) >= 2),
    ("env broken", lambda s: s.get("env") == "broken"),
]


def _env_state(task: dict, sanity_cache: dict[str, bool]) -> str:
    key = venv_key(task)
    if key in sanity_cache:
        return "passed" if sanity_cache[key] else "broken"
    return "not_curated"


def load_scores(path: str | Path) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def init_scores(tasks: list[dict], path: str | Path) -> None:
    """生成空白打分骨架（clarity / f2p_reach 待人工填，env 自动）。

    每题内嵌 `_context`（问题摘要 + F2P），人工打分无需再翻 tasks.json。
    仅 `clarity` / `f2p_reach` 参与评分，`_context` 被报告生成忽略。
    """
    p = Path(path)
    existing = load_scores(p)
    for t in tasks:
        iid = t["instance_id"]
        if iid not in existing:
            existing[iid] = {"clarity": None, "f2p_reach": None}
        ps = t.get("problem_statement", "")
        existing[iid]["_context"] = {
            "problem": (ps.strip().splitlines()[0] if ps.strip() else "")[:200],
            "f2p": t.get("FAIL_TO_PASS", [])[:3],
        }
    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Score skeleton written to {p}")


def generate_report(
    tasks: list[dict],
    scores_path: str | Path,
    output_path: str | Path,
) -> None:
    scores = load_scores(scores_path)
    sanity = load_sanity_cache()

    env_state = {t["instance_id"]: _env_state(t, sanity) for t in tasks}

    def excluded(t: dict) -> tuple[bool, list[str]]:
        s = scores.get(t["instance_id"], {})
        s["env"] = env_state[t["instance_id"]]
        reasons = [label for label, fn in EXCLUDE_RULES if fn(s)]
        return bool(reasons), reasons

    lines: list[str] = []
    lines.append("# 任务集筛查报告（SWE-bench Verified 方法）")
    lines.append("")
    lines.append(f"总任务数: {len(tasks)}")
    lines.append(f"已打分: {sum(1 for t in tasks if t['instance_id'] in scores)}")
    lines.append("")
    lines.append(CRITERIA_TEXT)
    lines.append("")

    lines.append("## 逐任务\n")
    lines.append("| 任务 | 仓库 | 清晰度 | F2P 可达 | 环境 | 官方标注 | 官方判定 | 剔除? | 原因 |")
    lines.append("|------|------|--------|----------|------|----------|----------|-------|------|")
    official = load_official()
    for t in tasks:
        iid = t["instance_id"]
        s = scores.get(iid, {})
        env = env_state[iid]
        ex, reasons = excluded(t)
        clarity = s.get("clarity")
        f2p_reach = s.get("f2p_reach")
        off = official.get(iid)
        off_cell = "-"
        off_verdict = "-"
        if off:
            off_cell = f"{off['clarity']}/{off['f2p_reach']}"
            off_verdict = "✗ 剔除" if off["filter_out"] else "✓ 保留"
        lines.append(
            f"| {iid} | {t.get('repo', '')} "
            f"| {clarity if clarity is not None else '-'} "
            f"| {f2p_reach if f2p_reach is not None else '-'} "
            f"| {env} | {off_cell} | {off_verdict} "
            f"| {'⚠️' if ex else ''} | {'; '.join(reasons)} |"
        )

    kept = [t for t in tasks if not excluded(t)[0]]
    dropped = [t for t in tasks if excluded(t)[0]]
    off_filtered = [t for t in tasks if official.get(t["instance_id"], {}).get("filter_out")]
    lines.append("\n## 结论\n")
    lines.append(f"- 保留: {len(kept)} / {len(tasks)}")
    lines.append(f"- 剔除: {len(dropped)}")
    lines.append(f"- **官方标注应剔除（OpenAI SWE-bench Verified 评审）: {len(off_filtered)} 题**")
    lines.append("- 剔除任务:")
    for t in dropped:
        lines.append(f"  - `{t['instance_id']}` ({'; '.join(excluded(t)[1])})")
    if off_filtered:
        lines.append("- 官方标注剔除但本报告未剔除的任务（可人工复核）:")
        for t in off_filtered:
            if not excluded(t)[0]:
                lines.append(f"  - `{t['instance_id']}` (官方清晰度/测试={official[t['instance_id']]['clarity']}/{official[t['instance_id']]['f2p_reach']})")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit report saved to {output_path}")

    n_curated = sum(1 for st in env_state.values() if st == "not_curated")
    if n_curated:
        print(f"[hint] {n_curated} tasks have un-curated envs; "
              f"run a real eval pass first (or curate eval/env.py REPO_SETUP).")


def main() -> None:
    p = argparse.ArgumentParser(description="P0-5 任务质量筛查")
    p.add_argument("--tasks", default="eval/tasks.json")
    p.add_argument("--scores", default="eval/audit_scores.json")
    p.add_argument("--out", default="eval/audit_results.md")
    p.add_argument("--init", action="store_true", help="生成空白打分骨架")
    args = p.parse_args()

    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if args.init:
        init_scores(tasks, args.scores)
    else:
        generate_report(tasks, args.scores, args.out)


if __name__ == "__main__":
    main()

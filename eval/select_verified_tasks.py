"""从 SWE-bench Lite 中挑选 OpenAI 官方保留（filter_out=False）的 30 道任务。

数据源:
- SWE-bench_Lite（HF datasets）
- eval/swe_annotations_ensembled.csv（OpenAI 官方人工标注全集，1699 样本）

选择策略:
- 仅 filter_out=False（官方判定保留）
- 排除 django/django（过大）
- 按仓库配额选取，保证多样性；配额优先已策展环境（sympy/pylint）
- F2P 数 1-5 优先（考题聚焦）

输出: eval/tasks.json（全量 node id）+ eval/official_annotations.csv（仅选中 30 题）
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from datasets import load_dataset

REPO_QUOTAS: dict[str, int] = {
    "sympy/sympy": 14,
    "scikit-learn/scikit-learn": 6,
    "astropy/astropy": 3,
    "matplotlib/matplotlib": 3,
    "sphinx-doc/sphinx": 2,
    "pylint-dev/pylint": 1,
    "pytest-dev/pytest": 1,
}
SKIP_REPOS = {"django/django"}
# pytest 自家套件的 parametrize id 版本敏感（数据集 id 与 base_commit 的 pytest 版本不匹配），
# 导出时剥离参数后缀（名字级稳定，见 README 已知限制）
STRIP_PARAMS_REPOS = {"pytest-dev/pytest"}
TASKS_OUT = "eval/tasks.json"
ANNOT_OUT = "eval/official_annotations.csv"
ANNOT_SRC = "eval/swe_annotations_ensembled.csv"


def _load_annotations() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(ANNOT_SRC, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["instance_id"]] = r
    return out


def _p2p_hygiene(patch: str, p2p: list[str]) -> list[str]:
    """剔除被 test_patch 新增的 P2P（P2P 必须在 base_commit 上存在并通过）。

    sphinx-8721 类数据集缺陷：P2P 列表混入 gold patch 新增的测试。
    """
    added = set()
    for m in re.finditer(r"^\+\s*(?:def |    def )([A-Za-z_][\w]*)", patch, re.M):
        added.add(m.group(1))
    if not added:
        return p2p
    return [n for n in p2p if n.split("::")[-1].split("[")[0] not in added]


def main() -> None:
    ann = _load_annotations()
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")

    by_repo: dict[str, list[dict]] = {}
    for t in ds:
        iid = t["instance_id"]
        r = ann.get(iid)
        if not r or r["filter_out"].lower() != "false":
            continue
        if t["repo"] in SKIP_REPOS:
            continue
        f2p = json.loads(t["FAIL_TO_PASS"])
        p2p = _p2p_hygiene(t["test_patch"], json.loads(t["PASS_TO_PASS"]))
        if len(f2p) < 1 or len(f2p) > 5:
            continue
        if len(f2p) + len(p2p) > 100:   # P2P 批量跑（verify batch=True），上限放宽
            continue
        by_repo.setdefault(t["repo"], []).append({
            "instance_id": iid,
            "repo": t["repo"],
            "base_commit": t["base_commit"],
            "problem_statement": t["problem_statement"],
            "FAIL_TO_PASS": f2p,
            "PASS_TO_PASS": p2p,
            "test_patch": t["test_patch"],
            "environment_setup_commit": t.get("environment_setup_commit", ""),
            "_n_f2p": len(f2p),
        })

    for repo in by_repo:
        by_repo[repo].sort(key=lambda x: (x["_n_f2p"], x["instance_id"]))

    selected: list[dict] = []
    for repo, quota in REPO_QUOTAS.items():
        pool = by_repo.get(repo, [])
        picked = pool[:quota]
        selected.extend(picked)
        print(f"{repo:30s} kept={len(pool):3d} selected={len(picked)}")

    if len(selected) < 30:
        # 只从配额仓的剩余池补充，避免引入未策展环境的新仓库
        rest: list[dict] = []
        for repo, pool in by_repo.items():
            if repo in REPO_QUOTAS:
                rest.extend(pool[REPO_QUOTAS[repo]:])
        rest.sort(key=lambda x: (x["_n_f2p"], x["instance_id"]))
        selected.extend(rest[:30 - len(selected)])

    selected = selected[:30]
    print(f"\nTotal selected: {len(selected)}")

    for t in selected:
        t.pop("_n_f2p", None)
        if t["repo"] in STRIP_PARAMS_REPOS:
            for key in ("FAIL_TO_PASS", "PASS_TO_PASS"):
                t[key] = [n.split("[")[0] for n in t[key]]
    Path(TASKS_OUT).write_text(
        json.dumps(selected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 官方标注子集（与任务集一一对应）
    cols = ["instance_id", "underspecified", "false_negative", "difficulty",
            "filter_out", "underspecified_notes", "false_negative_notes",
            "other_major_issues"]
    with open(ANNOT_OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for t in selected:
            w.writerow({c: ann[t["instance_id"]].get(c, "") for c in cols})
    print(f"Wrote {TASKS_OUT} + {ANNOT_OUT}")


if __name__ == "__main__":
    main()

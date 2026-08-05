from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from eval.harness import load_tasks, run_eval
from eval.matrix import TaskResult, build_matrix
from eval.reporter import generate_report


def _save_results(results: list[TaskResult], out_dir: str = "runs/eval") -> str:
    """TaskResult 落盘：报告格式/逐题表迭代无需重跑 480 runs。"""
    path = Path(out_dir) / f"results_{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        [r.to_dict() for r in results], indent=1, ensure_ascii=False,
    ), encoding="utf-8")
    return str(path)


def _latest_results(out_dir: str = "runs/eval") -> list[TaskResult]:
    """resume 模式：读最近一次落盘的 results，合并成累计全量（断点续跑后报告不残缺）。"""
    files = sorted(Path(out_dir).glob("results_*.json"))
    if not files:
        return []
    try:
        return [TaskResult.from_dict(d) for d in json.loads(files[-1].read_text(encoding="utf-8"))]
    except Exception:
        return []


def main():
    p = argparse.ArgumentParser(description="XClaw Evaluation Harness")
    p.add_argument("--tasks", help="Path to tasks.json (not needed with --regen)")
    p.add_argument("--out", default="eval_report.md", help="Output report path")
    p.add_argument("--repeat", type=int, default=3, help="Repeat count per cell")
    p.add_argument("--fake", action="store_true", help="Use FakeBackend instead of real LLM")
    p.add_argument("--workdir", default="eval/.workdir", help="Base directory for task repos")
    p.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    p.add_argument("--max-turns", type=int, default=500,
                   help="Max agent turns per run (fuse: only stops out-of-control "
                        "runs; normal runs stop via end_turn/supervisor_done)")
    p.add_argument("--fresh", action="store_true",
                   help="Ignore manifest and rerun already-done cells")
    p.add_argument("--max-cost", type=float, default=None,
                   help="Global cost budget in USD; stop when exceeded (input/output price-based)")
    p.add_argument("--supervisor", action="store_true",
                   help="Enable Supervision Agent (ADR-0020): periodic + completion checks")
    p.add_argument("--supervisor-model", default=None,
                   help="Supervisor model (default: same as --model)")
    p.add_argument("--price-input", type=float, default=0.28,
                   help="USD per 1M input tokens (default: deepseek-chat approx)")
    p.add_argument("--price-output", type=float, default=1.10,
                   help="USD per 1M output tokens")
    p.add_argument("--regen", metavar="RESULTS_JSON",
                   help="Skip evaluation; regenerate report from saved results JSON")
    p.add_argument("--config", metavar="C_X_M_r0",
                   help="Run a single cell config (cell_label, e.g. C_X_M_r0); "
                        "default: full 2x2x2 matrix x repeat")
    p.add_argument("--instances", metavar="id1,id2",
                   help="Comma-separated instance_id filter (smoke runs)")
    p.add_argument("--design", default="ofat", choices=["ofat", "full"],
                   help="Ablation design: ofat=baseline+3 single-factor offs "
                        "(default); full=2x2x2 all combinations")
    p.add_argument("--ablation-tasks", metavar="JSON",
                   help="Tiered run: core tasks run baseline cell (k=--repeat), "
                        "ablation tasks run the 3 single-factor-off cells (k=--ablation-repeat)")
    p.add_argument("--ablation-repeat", type=int, default=2,
                   help="Repeats for ablation cells (core k stays --repeat)")
    p.add_argument("--price-cache", type=float, default=0.07,
                   help="USD per 1M cache-hit input tokens (DeepSeek auto prefix cache)")
    args = p.parse_args()

    if args.regen:
        raw = json.loads(Path(args.regen).read_text(encoding="utf-8"))
        results = [TaskResult.from_dict(d) for d in raw]
        generate_report(results, args.out)
        print(f"Regenerated report from {args.regen} -> {args.out}")
        return

    if not args.tasks:
        p.error("--tasks is required unless --regen is used")
    tasks = load_tasks(args.tasks)
    if args.instances:
        ids = {s.strip() for s in args.instances.split(",") if s.strip()}
        tasks = [t for t in tasks if t["instance_id"] in ids]
        print(f"Filtered to {len(tasks)} instances: {', '.join(t['instance_id'] for t in tasks)}")
    print(f"Loaded {len(tasks)} tasks from {args.tasks}")

    if args.config:
        from eval.matrix import EvalCell, parse_cell_label
        base = parse_cell_label(args.config)
        # --config + --repeat：扩展为 k 次重复（pass^k 用），r0..r{k-1}
        matrix = [EvalCell(base.compression, base.concurrency, base.repo_map, rep)
                  for rep in range(args.repeat)]
    else:
        matrix = build_matrix(args.repeat, design=args.design,
                              ablation_repeat=args.ablation_repeat)
    print(f"Matrix: {len(matrix)} cells ({matrix[0].__class__.__name__}, design={args.design})")

    if args.fake:
        print("Using FakeBackend (no API calls)")
        # Only run 1 task with 1 cell for quick validation
        tasks = tasks[:1]
        matrix = matrix[:1]

    # 分层运行：核心层只跑基线全开 cell（pass^k 主数字），消融层跑单变量关闭 cell
    if args.ablation_tasks and not args.config:
        baseline = [c for c in matrix if c.compression and c.concurrency and c.repo_map]
        ablation = [c for c in matrix if not (c.compression and c.concurrency and c.repo_map)]
        print(f"Tiered: core={len(tasks)} tasks x {len(baseline)} cells (k={len(baseline)}), "
              f"ablation={len(load_tasks(args.ablation_tasks))} tasks x {len(ablation)} cells")
        results = run_eval(
            tasks=tasks, matrix=baseline, workdir_base=args.workdir,
            use_fake=args.fake, model_name=args.model, max_turns=args.max_turns,
            resume=not args.fresh, max_cost=args.max_cost,
            price_input=args.price_input, price_output=args.price_output,
            price_cache=args.price_cache,
            supervisor=args.supervisor, supervisor_model=args.supervisor_model,
        )
        results += run_eval(
            tasks=load_tasks(args.ablation_tasks), matrix=ablation,
            workdir_base=args.workdir, use_fake=args.fake, model_name=args.model,
            max_turns=args.max_turns, resume=not args.fresh, max_cost=args.max_cost,
            price_input=args.price_input, price_output=args.price_output,
            price_cache=args.price_cache,
            supervisor=args.supervisor, supervisor_model=args.supervisor_model,
        )
    else:
        results = run_eval(
            tasks=tasks,
            matrix=matrix,
            workdir_base=args.workdir,
            use_fake=args.fake,
            model_name=args.model,
            max_turns=args.max_turns,
            resume=not args.fresh,
            max_cost=args.max_cost,
            price_input=args.price_input,
            price_output=args.price_output,
            price_cache=args.price_cache,
            supervisor=args.supervisor,
            supervisor_model=args.supervisor_model,
        )

    if not args.fresh and not args.fake:
        from eval.matrix import cell_label
        prev = _latest_results()
        seen = {(r.instance_id, cell_label(r.cell)) for r in results}
        merged = results + [r for r in prev
                            if (r.instance_id, cell_label(r.cell)) not in seen]
        results = merged

    passed = sum(1 for r in results if r.passed is True)
    failed = sum(1 for r in results if r.passed is False)
    errored = sum(1 for r in results if r.error)

    print(f"\nDone. Total: {len(results)} | Passed: {passed} | Failed: {failed} | Errors: {errored}")

    results_json = _save_results(results)
    print(f"Results saved to {results_json} (regen report: --regen {results_json})")

    generate_report(results, args.out)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.harness import load_tasks, run_eval
from eval.matrix import build_matrix
from eval.reporter import generate_report


def main():
    p = argparse.ArgumentParser(description="XClaw Evaluation Harness")
    p.add_argument("--tasks", required=True, help="Path to tasks.json")
    p.add_argument("--out", default="eval_report.md", help="Output report path")
    p.add_argument("--repeat", type=int, default=3, help="Repeat count per cell")
    p.add_argument("--fake", action="store_true", help="Use FakeBackend instead of real LLM")
    p.add_argument("--workdir", default="/tmp/xcode_eval", help="Base directory for task repos")
    p.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    args = p.parse_args()

    tasks = load_tasks(args.tasks)
    print(f"Loaded {len(tasks)} tasks from {args.tasks}")

    matrix = build_matrix(args.repeat)
    print(f"Matrix: {len(matrix)} cells ({matrix[0].__class__.__name__})")

    if args.fake:
        print("Using FakeBackend (no API calls)")
        # Only run 1 task with 1 cell for quick validation
        tasks = tasks[:1]
        matrix = matrix[:1]

    results = run_eval(
        tasks=tasks,
        matrix=matrix,
        workdir_base=args.workdir,
        use_fake=args.fake,
        model_name=args.model,
    )

    passed = sum(1 for r in results if r.passed is True)
    failed = sum(1 for r in results if r.passed is False)
    errored = sum(1 for r in results if r.error)

    print(f"\nDone. Total: {len(results)} | Passed: {passed} | Failed: {failed} | Errors: {errored}")

    generate_report(results, args.out)


if __name__ == "__main__":
    main()

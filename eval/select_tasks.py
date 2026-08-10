from __future__ import annotations

import json
import sys
from pathlib import Path


def select_tasks(
    output_path: str = "eval/tasks.json",
    max_tasks: int = 30,
) -> None:
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: datasets package not installed. Run: uv pip install datasets")
        sys.exit(1)

    print("Loading SWE-bench Lite...")
    tasks = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    print(f"Total instances: {len(tasks)}")

    # Selection criteria (from easiest to hardest, picking diverse repos)
    skip_repos = {"django/django"}  # too large
    selected: list[dict] = []

    for t in tasks:
        repo = t.get("repo", "")
        fail_to_pass = json.loads(t.get("FAIL_TO_PASS", "[]"))
        pass_to_pass = json.loads(t.get("PASS_TO_PASS", "[]"))

        # Skip large repos
        if repo in skip_repos:
            continue
        # Prefer tasks with reasonable test count (1-5 tests)
        total_tests = len(fail_to_pass) + len(pass_to_pass)
        if total_tests > 10:
            continue
        # Prefer tasks with FAIL_TO_PASS tests (clearly defined fix)
        if len(fail_to_pass) < 1:
            continue

        selected.append({
            "instance_id": t["instance_id"],
            "repo": repo,
            "base_commit": t["base_commit"],
            "problem_statement": t["problem_statement"],
            "FAIL_TO_PASS": json.loads(t.get("FAIL_TO_PASS", "[]")),
            "PASS_TO_PASS": json.loads(t.get("PASS_TO_PASS", "[]")),
            "test_patch": t.get("test_patch", ""),
            "environment_setup_commit": t.get("environment_setup_commit", ""),
        })

        if len(selected) >= max_tasks:
            break

    print(f"Selected {len(selected)} tasks")

    # Save
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved to {out}")

    # Summary
    repos = set(t["repo"] for t in selected)
    print(f"Repos: {', '.join(sorted(repos))}")
    fail_tests = sum(len(t.get("FAIL_TO_PASS", [])) for t in selected)
    pass_tests = sum(len(t.get("PASS_TO_PASS", [])) for t in selected)
    print(f"Total tests: {fail_tests + pass_tests} ({fail_tests} fail-to-pass, {pass_tests} pass-to-pass)")


if __name__ == "__main__":
    select_tasks()

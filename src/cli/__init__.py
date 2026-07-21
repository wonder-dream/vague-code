from __future__ import annotations

import argparse
import sys

from dotenv import dotenv_values

from src.agent.backend import create_deepseek_backend
from src.agent.config import AgentConfig
from src.agent.loop import Agent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xcode", description="Coding Agent (Xcode)")
    parser.add_argument("task", help="Task description for the agent")
    parser.add_argument("workdir", nargs="?", default=".", help="Workspace root directory")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    parser.add_argument("--max-turns", type=int, default=20, help="Maximum turns")
    parser.add_argument("--db-path", default="runs/runs.db", help="SQLite database path")
    parser.add_argument("--export-jsonl", help="Export trajectory to JSONL file path")

    args = parser.parse_args(argv)

    api_key = _resolve_api_key()
    if not api_key:
        print("Error: DEEPSEEK_API_KEY not found. Set it in .env or environment.", file=sys.stderr)
        sys.exit(1)

    try:
        config = AgentConfig(
            model=args.model,
            max_turns=args.max_turns,
            db_path=args.db_path,
        )

        backend = create_deepseek_backend(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout_s=config.turn_timeout_s,
        )

        agent = Agent(config, backend)
        traj = agent.run(task=args.task, workdir=args.workdir)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)

    last_llm = [e for e in traj.events if e.type == "llm_response"]
    if last_llm:
        blocks = last_llm[-1].payload.get("blocks", [])
        for b in blocks:
            if b.get("type") == "text":
                print(b.get("text", ""))

    run_end = [e for e in traj.events if e.type == "run_end"]
    reason = run_end[0].payload.get("reason", "?") if run_end else "?"
    print(f"\n— Run {traj.run_id} finished, reason: {reason}", file=sys.stderr)

    if args.export_jsonl:
        traj.export_jsonl(args.export_jsonl)
        print(f"  Trajectory exported: {args.export_jsonl}", file=sys.stderr)


def _resolve_api_key() -> str | None:
    env_file = dotenv_values()
    key = env_file.get("DEEPSEEK_API_KEY")
    if key:
        return key
    import os
    return os.environ.get("DEEPSEEK_API_KEY")

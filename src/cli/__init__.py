from __future__ import annotations

import argparse
import sys

from dotenv import dotenv_values
from rich.console import Console

from src.agent.backend import create_deepseek_backend
from src.agent.config import AgentConfig, TransportConfig
from src.agent.ir import dispatch_event
from src.agent.loop import Agent
from src.cli.renderer import RichStreamVisitor


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xcode", description="Coding Agent (Xcode)")
    parser.add_argument("task", help="Task description for the agent")
    parser.add_argument("workdir", nargs="?", default=".", help="Workspace root directory")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    parser.add_argument("--max-turns", type=int, default=20, help="Maximum turns")
    parser.add_argument("--db-path", default="runs/runs.db", help="SQLite database path")
    parser.add_argument("--export-jsonl", help="Export trajectory to JSONL file path")
    stream_grp = parser.add_mutually_exclusive_group()
    stream_grp.add_argument("--stream", action="store_true", dest="stream", default=True,
                            help="Enable streaming output (default)")
    stream_grp.add_argument("--no-stream", action="store_false", dest="stream",
                            help="Disable streaming output")
    parser.add_argument("--verbose", action="store_true", help="Show model info")

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
            transport=TransportConfig(stream=args.stream),
        )

        backend = create_deepseek_backend(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout_s=config.turn_timeout_s,
        )

        agent = Agent(config, backend)
        console = Console()
        visitor = RichStreamVisitor(console, verbose=args.verbose)
        handle = agent.start(task=args.task, workdir=args.workdir)
        for ev in handle:
            dispatch_event(ev, visitor)
        traj = handle.trajectory
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)

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

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values
from rich.console import Console

from src.agent.backend import create_deepseek_backend
from src.agent.config import AgentConfig, TransportConfig
from src.agent.ir import dispatch_event
from src.agent.loop import Agent
from src.cli.renderer import RichStreamVisitor


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="xcode", description="Coding Agent (Xcode)")
    parser.add_argument("task", nargs="?", default="", help="Task description for the agent")
    parser.add_argument("workdir", nargs="?", default=".", help="Workspace root directory")
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume a previous run by run ID")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    parser.add_argument("--max-turns", type=int, default=20, help="Maximum turns")
    parser.add_argument("--db-path", default="runs/runs.db", help="SQLite database path")
    parser.add_argument("--export-jsonl", help="Export trajectory to JSONL file path")
    stream_grp = parser.add_mutually_exclusive_group()
    stream_grp.add_argument("--stream", action="store_true", dest="stream", default=True,
                            help="Enable streaming output (default)")
    stream_grp.add_argument("--no-stream", action="store_false", dest="stream",
                            help="Disable streaming output")
    retry_grp = parser.add_mutually_exclusive_group()
    retry_grp.add_argument("--retry", action="store_true", dest="retry", default=True,
                           help="Enable retry on transient errors (default)")
    retry_grp.add_argument("--no-retry", action="store_false", dest="retry",
                           help="Disable retry")
    parser.add_argument("--retry-max-attempts", type=int, default=5,
                        help="Maximum number of retry attempts")
    parser.add_argument("--retry-base-s", type=float, default=2.0,
                        help="Base delay for exponential backoff (seconds)")
    parser.add_argument("--retry-max-delay-s", type=float, default=120.0,
                        help="Maximum delay between retries (seconds)")
    parser.add_argument("--timeout-s", type=float, default=120.0,
                        help="Per-turn LLM call timeout (seconds)")
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
            transport=TransportConfig(
                stream=args.stream,
                timeout_s=args.timeout_s,
                retry_enabled=args.retry,
                retry_max_attempts=args.retry_max_attempts,
                retry_base_s=args.retry_base_s,
                retry_max_delay_s=args.retry_max_delay_s,
            ),
        )

        backend = create_deepseek_backend(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout_s=config.transport.timeout_s,
        )

        agent = Agent(config, backend)
        console = Console()
        visitor = RichStreamVisitor(console, verbose=args.verbose)

        if args.resume:
            from src.agent.trajectory import Trajectory
            traj = Trajectory.from_db(args.resume, config.db_path)
            traj = agent.resume(traj)
            reason = "resumed"
        else:
            if not args.task:
                parser.error("task is required unless --resume is used")
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
        export_path = Path(args.export_jsonl)
        if export_path.is_dir():
            print(f"Error: --export-jsonl path is a directory: '{export_path}'", file=sys.stderr)
            sys.exit(1)
        traj.export_jsonl(str(export_path))
        print(f"  Trajectory exported: {export_path}", file=sys.stderr)


def _resolve_api_key() -> str | None:
    env_file = dotenv_values()
    key = env_file.get("DEEPSEEK_API_KEY")
    if key:
        return key
    import os
    return os.environ.get("DEEPSEEK_API_KEY")

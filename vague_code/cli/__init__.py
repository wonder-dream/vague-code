from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values
from rich.console import Console

from vague_code.agent.backend import ModelBackend, create_anthropic_backend, create_deepseek_backend
from vague_code.agent.config import AgentConfig, TransportConfig
from vague_code.agent.ir import dispatch_event
from vague_code.agent.loop import Agent
from vague_code.cli.renderer import RichStreamVisitor


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "chat":
        _chat_main(argv[1:])
        return
    if argv and argv[0] == "tui":
        _tui_main(argv[1:])
        return

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(prog="vague-code", description="Coding Agent (Xcode)")
    parser.add_argument("task", nargs="?", default="", help="Task description for the agent")
    parser.add_argument("workdir", nargs="?", default=".", help="Workspace root directory")
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume a previous run by run ID")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    parser.add_argument("--max-turns", type=int, default=None,
                        help=f"Maximum turns (default: {AgentConfig.max_turns})")
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
    parser.add_argument("--provider", default="deepseek", choices=["deepseek", "anthropic"],
                        help="Model provider (default: deepseek)")
    parser.add_argument("--no-repo-map", action="store_true", help="Disable repo map symbol index")
    parser.add_argument("--repo-map-tokens", type=int, default=1000,
                        help="Max tokens for the injected repo map (default: 1000)")
    parser.add_argument("--mode", default="normal", choices=["safe", "normal", "autoedit", "auto"],
                        help="Permission mode (default: normal; auto lets the agent edit unattended)")
    parser.add_argument("--verbose", action="store_true", help="Show model info")

    args = parser.parse_args(argv)

    if not args.task and not args.resume:
        parser.error("task is required unless --resume is used")

    try:
        api_key = _resolve_api_key(args.provider)
        if not api_key:
            key_name = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "DEEPSEEK_API_KEY"
            print(f"Error: {key_name} not found. Set it in .env or environment.", file=sys.stderr)
            sys.exit(1)

        config = AgentConfig(
            model=args.model,
            max_turns=args.max_turns if args.max_turns is not None else AgentConfig.max_turns,
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
        config.repo_map.enabled = not args.no_repo_map
        config.repo_map.max_map_tokens = args.repo_map_tokens
        config.permission_mode = args.mode

        backend: ModelBackend
        if args.provider == "anthropic":
            backend = create_anthropic_backend(  # type: ignore[assignment]
                api_key=api_key,
                base_url="https://api.deepseek.com/anthropic",
                timeout_s=config.transport.timeout_s,
            )
        else:
            backend = create_deepseek_backend(  # type: ignore[assignment]
                api_key=api_key,
                base_url="https://api.deepseek.com",
                timeout_s=config.transport.timeout_s,
            )

        agent = Agent(config, backend)
        for rule in _load_permission_rules(args.workdir):
            agent.add_permission_rule(rule["pattern"], rule.get("action", "allow"))
        console = Console()
        visitor = RichStreamVisitor(console, verbose=args.verbose)

        if args.resume:
            from vague_code.agent.trajectory import Trajectory
            traj = Trajectory.from_db(args.resume, config.db_path)
            traj = agent.resume(traj)
            reason = "resumed"
        else:
            handle = agent.start(task=args.task, workdir=args.workdir)
            for ev in handle:
                dispatch_event(ev, visitor)
            traj = handle.trajectory

    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        run_end = [e for e in traj.events if e.type == "run_end"]
        reason = run_end[0].payload.get("reason", "?") if run_end else "?"
        print(f"Run {traj.run_id} finished, reason: {reason}", file=sys.stderr)

    if args.export_jsonl:
        export_path = Path(args.export_jsonl)
        if export_path.is_dir():
            print(f"Error: --export-jsonl path is a directory: '{export_path}'", file=sys.stderr)
            sys.exit(1)
        traj.export_jsonl(str(export_path))
        print(f"  Trajectory exported: {export_path}", file=sys.stderr)


def _tui_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="vague-code tui", description="vague-code TUI mode")
    parser.add_argument("task", nargs="?", default="", help="Task description for the agent")
    parser.add_argument("workdir", nargs="?", default=".", help="Workspace root directory")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    parser.add_argument("--max-turns", type=int, default=None,
                        help=f"Maximum turns (default: {AgentConfig.max_turns})")
    parser.add_argument("--db-path", default="runs/runs.db", help="SQLite database path")
    parser.add_argument("--provider", default="deepseek", choices=["deepseek", "anthropic"],
                        help="Model provider (default: deepseek)")
    parser.add_argument("--timeout-s", type=float, default=120.0,
                        help="Per-turn LLM call timeout (seconds)")
    parser.add_argument("--retry-max-attempts", type=int, default=5,
                        help="Maximum number of retry attempts")
    parser.add_argument("--retry-base-s", type=float, default=2.0,
                        help="Base delay for exponential backoff (seconds)")
    parser.add_argument("--retry-max-delay-s", type=float, default=120.0,
                        help="Maximum delay between retries (seconds)")
    parser.add_argument("--mode", default="normal", choices=["safe", "normal", "autoedit", "auto"],
                        help="Permission mode (default: normal; auto lets the agent edit unattended)")

    args = parser.parse_args(argv)

    api_key = _resolve_api_key(args.provider)
    if not api_key:
        key_name = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "DEEPSEEK_API_KEY"
        print(f"Error: {key_name} not found. Set it in .env or environment.", file=sys.stderr)
        sys.exit(1)

    config = AgentConfig(
        model=args.model,
        max_turns=args.max_turns if args.max_turns is not None else AgentConfig.max_turns,
        db_path=args.db_path,
    )
    config.transport.timeout_s = args.timeout_s
    config.transport.retry_max_attempts = args.retry_max_attempts
    config.transport.retry_base_s = args.retry_base_s
    config.transport.retry_max_delay_s = args.retry_max_delay_s
    config.permission_mode = args.mode

    if args.provider == "anthropic":
        backend = create_anthropic_backend(
            api_key=api_key,
            base_url="https://api.deepseek.com/anthropic",
            timeout_s=config.transport.timeout_s,
        )
    else:
        backend = create_deepseek_backend(  # type: ignore[assignment,arg-type]
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout_s=config.transport.timeout_s,
        )

    from vague_code.tui import main as tui_main
    tui_main(task=args.task, workdir=args.workdir, config=config, backend=backend)


def _chat_main(argv: list[str]) -> None:
    """`vague-code chat`：会话内连续对话 REPL（ADR-0025）。

    输入普通消息即一轮对话（上下文延续）；`exit`/Ctrl+C/Ctrl+D 退出（自动结束会话）；
    `/new` 开始新会话；`/resume <run_id>` 恢复历史会话。
    """
    parser = argparse.ArgumentParser(prog="vague-code chat", description="vague-code interactive chat")
    parser.add_argument("workdir", nargs="?", default=".", help="Workspace root directory")
    parser.add_argument("--resume", metavar="RUN_ID", help="Resume a previous chat session")
    parser.add_argument("--model", default="deepseek-v4-flash", help="Model name")
    parser.add_argument("--max-turns", type=int, default=None,
                        help=f"Maximum turns (default: {AgentConfig.max_turns})")
    parser.add_argument("--db-path", default="runs/runs.db", help="SQLite database path")
    parser.add_argument("--provider", default="deepseek", choices=["deepseek", "anthropic"],
                        help="Model provider (default: deepseek)")
    parser.add_argument("--timeout-s", type=float, default=120.0,
                        help="Per-turn LLM call timeout (seconds)")
    parser.add_argument("--retry-max-attempts", type=int, default=5,
                        help="Maximum number of retry attempts")
    parser.add_argument("--retry-base-s", type=float, default=2.0,
                        help="Base delay for exponential backoff (seconds)")
    parser.add_argument("--retry-max-delay-s", type=float, default=120.0,
                        help="Maximum delay between retries (seconds)")
    parser.add_argument("--mode", default="normal", choices=["safe", "normal", "autoedit", "auto"],
                        help="Permission mode (default: normal; auto lets the agent edit unattended)")

    args = parser.parse_args(argv)

    api_key = _resolve_api_key(args.provider)
    if not api_key:
        key_name = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "DEEPSEEK_API_KEY"
        print(f"Error: {key_name} not found. Set it in .env or environment.", file=sys.stderr)
        sys.exit(1)

    config = AgentConfig(
        model=args.model,
        max_turns=args.max_turns if args.max_turns is not None else AgentConfig.max_turns,
        db_path=args.db_path,
    )
    config.transport.timeout_s = args.timeout_s
    config.transport.retry_max_attempts = args.retry_max_attempts
    config.transport.retry_base_s = args.retry_base_s
    config.transport.retry_max_delay_s = args.retry_max_delay_s
    config.permission_mode = args.mode

    if args.provider == "anthropic":
        backend = create_anthropic_backend(  # type: ignore[assignment]
            api_key=api_key,
            base_url="https://api.deepseek.com/anthropic",
            timeout_s=config.transport.timeout_s,
        )
    else:
        backend = create_deepseek_backend(  # type: ignore[assignment,arg-type]
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout_s=config.transport.timeout_s,
        )

    agent = Agent(config, backend)
    for rule in _load_permission_rules(args.workdir):
        agent.add_permission_rule(rule["pattern"], rule.get("action", "allow"))
    console = Console()
    visitor = RichStreamVisitor(console, verbose=False)

    def run_handle(handle) -> None:
        try:
            for ev in handle:
                dispatch_event(ev, visitor)
        except KeyboardInterrupt:
            handle.close()
            print("\n(interrupted)", file=sys.stderr)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)

    if args.resume:
        try:
            run_handle(agent.chat_resume(args.resume))
        except Exception as e:
            print(f"Resume failed: {e}", file=sys.stderr)
            agent.chat_end()
            return
        print(f"[会话 {args.resume} 已恢复，可继续对话]", file=sys.stderr)

    while True:
        try:
            text = console.input("> ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            agent.chat_end()
            return
        text = text.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            agent.chat_end()
            return
        if text == "/new":
            agent.chat_end()
            print("[已开始新会话]", file=sys.stderr)
            continue
        if text.startswith("/resume"):
            parts = text.split(maxsplit=1)
            run_id = parts[1].strip() if len(parts) > 1 else ""
            if not run_id:
                print("[用法: /resume <run_id>]", file=sys.stderr)
                continue
            agent.chat_end()
            try:
                run_handle(agent.chat_resume(run_id))
                print(f"[会话 {run_id} 已恢复，可继续对话]", file=sys.stderr)
            except Exception as e:
                print(f"Resume failed: {e}", file=sys.stderr)
            continue
        if text.startswith("/"):
            print(f"[未知命令: {text}（可用 /new /resume /exit）]", file=sys.stderr)
            continue
        run_handle(agent.chat(text, args.workdir))


def _resolve_api_key(provider: str) -> str | None:
    env_file = dotenv_values()
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "DEEPSEEK_API_KEY"
    key = env_file.get(key_name)
    if key:
        return key
    import os
    return os.environ.get(key_name)


def _load_permission_rules(workdir: str) -> list[dict]:
    """Load `.agent/permission-rules.json` from the workspace (same path as TUI)."""
    import json as _json
    try:
        path = Path(workdir) / ".agent" / "permission-rules.json"
        if not path.is_file():
            return []
        data = _json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []

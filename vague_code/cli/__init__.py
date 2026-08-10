from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values
from rich.console import Console

from vague_code.agent.backend import (
    ModelBackend,
    create_anthropic_backend,
    create_deepseek_backend,
    create_responses_backend,
)
from vague_code.agent.config import AgentConfig, TransportConfig
from vague_code.agent.ir import dispatch_event
from vague_code.agent.loop import Agent
from vague_code.cli.renderer import RichStreamVisitor
from vague_code.config import load_config, write_init_template

_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("https://api.deepseek.com/anthropic", "ANTHROPIC_API_KEY"),
}

_DEFAULT_MODEL = "deepseek-v4-flash"


def _provider_settings(
    provider: str,
    base_url: str | None,
    api_key_env: str | None,
    config: dict | None = None,
) -> tuple[str, str, str]:
    """返回 (base_url, key_env, protocol)。自定义 provider 查配置文件，内置走默认表。"""
    spec = None
    if config:
        spec = config.get("providers", {}).get(provider)
    if spec:
        return (
            base_url or str(spec.get("baseUrl") or ""),
            api_key_env or str(spec.get("apiKeyEnv") or ""),
            str(spec.get("protocol") or "openai"),
        )
    default_url, default_env = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["deepseek"])
    protocol = "anthropic" if provider == "anthropic" else "openai"
    return base_url or default_url, api_key_env or default_env, protocol


def _resolve_config(model: str | None, provider: str | None, workdir: str) -> tuple[str, str, dict]:
    """从配置文件补缺省：返回 (model, provider, config)。"""
    cfg = load_config(workdir)
    provider = provider or str(cfg.get("defaultProvider") or "deepseek")
    model = model or str(cfg.get("defaultModel") or "") or _DEFAULT_MODEL
    return model, provider, cfg


def _build_backend(provider: str, api_key: str, base_url: str, protocol: str, timeout_s: float) -> ModelBackend:
    if protocol == "anthropic":
        return create_anthropic_backend(  # type: ignore[return-value]
            api_key=api_key,
            base_url=base_url,
            timeout_s=timeout_s,
        )
    if protocol == "responses":
        return create_responses_backend(  # type: ignore[return-value]
            api_key=api_key,
            base_url=base_url,
            timeout_s=timeout_s,
        )
    return create_deepseek_backend(  # type: ignore[return-value]
        api_key=api_key,
        base_url=base_url,
        timeout_s=timeout_s,
    )


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "chat":
        _chat_main(argv[1:])
        return
    if argv and argv[0] == "tui":
        _tui_main(argv[1:])
        return
    if argv and argv[0] == "init":
        _init_main(argv[1:])
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
    parser.add_argument("--provider", default=None,
                        help="Model provider (builtin: deepseek/openai/anthropic, or any name from vague-code.json)")
    parser.add_argument("--base-url", default=None, help="Override the provider base URL (any OpenAI-compatible endpoint)")
    parser.add_argument("--api-key-env", default=None, help="Env var name holding the API key (default: per provider)")
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
        model, provider, file_cfg = _resolve_config(args.model, args.provider, args.workdir)
        base_url, key_env, protocol = _provider_settings(provider, args.base_url, args.api_key_env, file_cfg)
        api_key = _resolve_api_key(key_env)
        if not api_key:
            print(f"Error: {key_env} not found. Set it in .env or environment.", file=sys.stderr)
            sys.exit(1)

        config = AgentConfig(
            model=model,
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

        backend: ModelBackend = _build_backend(
            provider, api_key, base_url, protocol, config.transport.timeout_s,
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
    parser.add_argument("--provider", default=None,
                        help="Model provider (builtin: deepseek/openai/anthropic, or any name from vague-code.json)")
    parser.add_argument("--base-url", default=None, help="Override the provider base URL (any OpenAI-compatible endpoint)")
    parser.add_argument("--api-key-env", default=None, help="Env var name holding the API key (default: per provider)")
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

    model, provider, file_cfg = _resolve_config(args.model, args.provider, args.workdir)
    base_url, key_env, protocol = _provider_settings(provider, args.base_url, args.api_key_env, file_cfg)
    api_key = _resolve_api_key(key_env)
    if not api_key:
        print(f"Error: {key_env} not found. Set it in .env or environment.", file=sys.stderr)
        sys.exit(1)

    config = AgentConfig(
        model=model,
        max_turns=args.max_turns if args.max_turns is not None else AgentConfig.max_turns,
        db_path=args.db_path,
    )
    config.transport.timeout_s = args.timeout_s
    config.transport.retry_max_attempts = args.retry_max_attempts
    config.transport.retry_base_s = args.retry_base_s
    config.transport.retry_max_delay_s = args.retry_max_delay_s
    config.permission_mode = args.mode

    backend: ModelBackend = _build_backend(
        provider, api_key, base_url, protocol, config.transport.timeout_s,
    )

    from vague_code.tui import main as tui_main
    tui_main(task=args.task, workdir=args.workdir, config=config, backend=backend,
             provider=provider, file_config=file_cfg)


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
    parser.add_argument("--provider", default=None,
                        help="Model provider (builtin: deepseek/openai/anthropic, or any name from vague-code.json)")
    parser.add_argument("--base-url", default=None, help="Override the provider base URL (any OpenAI-compatible endpoint)")
    parser.add_argument("--api-key-env", default=None, help="Env var name holding the API key (default: per provider)")
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

    model, provider, file_cfg = _resolve_config(args.model, args.provider, args.workdir)
    base_url, key_env, protocol = _provider_settings(provider, args.base_url, args.api_key_env, file_cfg)
    api_key = _resolve_api_key(key_env)
    if not api_key:
        print(f"Error: {key_env} not found. Set it in .env or environment.", file=sys.stderr)
        sys.exit(1)

    config = AgentConfig(
        model=model,
        max_turns=args.max_turns if args.max_turns is not None else AgentConfig.max_turns,
        db_path=args.db_path,
    )
    config.transport.timeout_s = args.timeout_s
    config.transport.retry_max_attempts = args.retry_max_attempts
    config.transport.retry_base_s = args.retry_base_s
    config.transport.retry_max_delay_s = args.retry_max_delay_s
    config.permission_mode = args.mode

    backend: ModelBackend = _build_backend(
        provider, api_key, base_url, protocol, config.transport.timeout_s,
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


def _resolve_api_key(key_env: str) -> str | None:
    env_file = dotenv_values()
    key = env_file.get(key_env)
    if key:
        return key
    import os
    return os.environ.get(key_env)


def _init_main(argv: list[str]) -> None:
    """`vague-code init`：生成 vague-code.json 配置模板（ADR-0033）。"""
    parser = argparse.ArgumentParser(
        prog="vague-code init",
        description="Generate a vague-code.json provider config template",
    )
    parser.add_argument("--path", default=None, help="Output path (default: ./vague-code.json)")
    args = parser.parse_args(argv)

    out = write_init_template(args.path or "vague-code.json")
    print(f"Created {out}")
    print("Edit it to fill in your providers (e.g. a relay base URL + key env name),")
    print("then just run: vague-code tui")


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

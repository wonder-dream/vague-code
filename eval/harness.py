from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from eval.matrix import EvalCell, TaskResult


def load_tasks(tasks_path: str) -> list[dict[str, Any]]:
    with open(tasks_path, encoding="utf-8") as f:
        return json.load(f)


def _extract_stats(trajectory_path: str) -> dict[str, Any]:
    """从 SQLite trajectory 提取统计指标."""
    import sqlite3

    try:
        conn = sqlite3.connect(trajectory_path)
        events = conn.execute(
            "SELECT turn, type, payload FROM events ORDER BY rowid"
        ).fetchall()
        conn.close()
    except Exception:
        return {"error": "cant_read_trajectory"}

    stats: dict[str, Any] = {
        "total_turns": 0,
        "tool_calls": 0,
        "code_search_calls": 0,
        "compression_events": 0,
        "stale_snip_reclaimed": 0,
        "microcompact_reclaimed": 0,
        "structured_snip_reclaimed": 0,
        "auto_compact_reclaimed": 0,
        "truncate_reclaimed": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "permission_checks": 0,
        "run_end_reason": "",
    }

    for turn, etype, payload_json in events:
        payload = json.loads(payload_json)
        if etype == "turn_start":
            stats["total_turns"] += 1
        elif etype == "tool_call":
            stats["tool_calls"] += 1
            if payload.get("name") == "code_search":
                stats["code_search_calls"] += 1
        elif etype == "compression":
            stats["compression_events"] += 1
            layer = payload.get("layer", "")
            reclaimed = payload.get("before_tokens", 0) - payload.get("after_tokens", 0)
            if reclaimed > 0:
                key = f"{layer}_reclaimed"
                if key in stats:
                    stats[key] += reclaimed
        elif etype == "llm_response":
            usage = payload.get("usage", {})
            stats["total_input_tokens"] += usage.get("input_tokens", 0)
            stats["total_output_tokens"] += usage.get("output_tokens", 0)
        elif etype == "permission_check":
            stats["permission_checks"] += 1
        elif etype == "run_end":
            stats["run_end_reason"] = payload.get("reason", "")

    return stats


def _set_workdir(task: dict, base_dir: str) -> str:
    """Clone repo at base_commit into workdir."""
    repo_url = f"https://github.com/{task['repo']}.git"
    commit = task["base_commit"]
    workdir = str(Path(base_dir) / task["instance_id"])

    if Path(workdir).exists():
        shutil.rmtree(workdir)

    subprocess.run(
        ["git", "clone", repo_url, workdir],
        capture_output=True, check=True, timeout=120,
    )
    subprocess.run(
        ["git", "checkout", commit],
        cwd=workdir, capture_output=True, check=True, timeout=30,
    )
    return workdir


def run_eval(
    tasks: list[dict],
    matrix: list[EvalCell],
    workdir_base: str,
    use_fake: bool = False,
    model_name: str = "deepseek-v4-flash",
) -> list[TaskResult]:
    from src.agent.loop import Agent
    from src.agent.config import AgentConfig, MemoryConfig
    from src.agent.backend import DeepSeekBackend
    from src.agent.ir import ModelResponse, NormalizedUsage, StopReason, TextBlock

    results: list[TaskResult] = []

    for cell in matrix:
        for task in tasks[:1] if use_fake else tasks:
            instance_id = task["instance_id"]

            try:
                workdir = _set_workdir(task, workdir_base)
            except Exception as e:
                results.append(TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None, error=f"checkout failed: {e}",
                ))
                continue

            config = AgentConfig(
                max_turns=50,
                model=model_name,
                concurrent_tools=cell.concurrency,
                permission_mode="auto",
                memory=MemoryConfig(enabled=False),
            )
            config.compression.enabled = cell.compression
            config.repo_map.enabled = cell.repo_map

            if use_fake:
                from src.agent.ir import Message

                class _FakeBackend:
                    def __init__(self):
                        self.call_count = 0
                    def complete(self, messages, tools=None, config=None) -> ModelResponse:
                        self.call_count += 1
                        return ModelResponse(
                            message=Message(role="assistant", content=[TextBlock(text="ok")]),
                            stop_reason=StopReason.end_turn,
                            usage=NormalizedUsage(input_tokens=10, output_tokens=5),
                        )
                backend = _FakeBackend()
            else:
                backend = DeepSeekBackend()

            try:
                agent = Agent(config, backend)
                traj = agent.run(task["problem_statement"], workdir)
                stats = _extract_stats(traj.config.db_path)
                stats["instance_id"] = instance_id
                results.append(TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None if use_fake else True,  # fake 不判 pass/fail
                    stats=stats,
                    trajectory_path=traj.config.db_path,
                ))
            except Exception as e:
                results.append(TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None, error=str(e),
                ))

    return results

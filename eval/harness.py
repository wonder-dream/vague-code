from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from eval.env import EnvNotCurated, ensure_env, venv_key
from eval.matrix import EvalCell, TaskResult, cell_label
from eval.verify import (
    SanityResult,
    load_sanity_cache,
    reset_workdir,
    sanity_gate,
    save_sanity_cache,
    verify_run,
)


def load_tasks(tasks_path: str) -> list[dict[str, Any]]:
    with open(tasks_path, encoding="utf-8") as f:
        return json.load(f)


def _run_db_path(instance_id: str, cell: EvalCell) -> str:
    """每个 run 独立的 SQLite 轨迹库，供离线判题/指标/judge 定位。

    位于 runs/eval/ 下（gitignore），与被克隆的任务仓库隔离，
    不受 P0-2 的 git clean -fdx 影响。
    """
    label = f"{instance_id}__{cell_label(cell)}"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)
    return str(Path("runs") / "eval" / f"{safe}.db")


def _build_deepseek_backend(model_name: str):
    """按项目约定（.env 优先于环境变量）解析 API key 构建真实后端。"""
    import os

    from dotenv import dotenv_values

    key = (dotenv_values().get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set (set it in .env or environment)")
    from src.agent.backend import create_deepseek_backend

    return create_deepseek_backend(api_key=key, base_url="https://api.deepseek.com", timeout_s=120.0)


def _cached_sanity(task: dict, workdir: str, env: Any) -> SanityResult:
    """sanity gate 结果按 (repo, base_commit) 缓存，避免每 repeat 重跑。"""
    cache = load_sanity_cache()
    key = venv_key(task)
    if key in cache:
        return SanityResult(ok=cache[key], reasons=[] if cache[key] else ["cached env_broken"])
    res = sanity_gate(task, workdir, env)
    cache[key] = res.ok
    save_sanity_cache(cache)
    return res


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


def _set_workdir(task: dict, base_dir: str, use_fake: bool = False) -> str:
    """Clone repo at base_commit into workdir (fake 模式跳过克隆，用临时空目录)."""
    repo_url = f"https://github.com/{task['repo']}.git"
    commit = task["base_commit"]
    workdir = str(Path(base_dir) / task["instance_id"])

    if use_fake:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        return workdir

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
    from src.agent.ir import ModelResponse, NormalizedUsage, StopReason, TextBlock

    results: list[TaskResult] = []

    for cell in matrix:
        for task in tasks[:1] if use_fake else tasks:
            instance_id = task["instance_id"]

            try:
                workdir = _set_workdir(task, workdir_base, use_fake=use_fake)
            except Exception as e:
                results.append(TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None, error=f"checkout failed: {e}",
                ))
                continue

            # P0-2: 显式状态隔离，保证 k 次重复起跑状态逐字节一致（pass^k 前提）
            reset_workdir(workdir)

            env = None
            if not use_fake:
                try:
                    env = ensure_env(task, workdir)
                except EnvNotCurated as e:
                    results.append(TaskResult(
                        instance_id=instance_id, cell=cell,
                        passed=False, error=str(e), verdict_reason="env_broken",
                    ))
                    continue
                except Exception as e:
                    results.append(TaskResult(
                        instance_id=instance_id, cell=cell,
                        passed=False, error=f"env setup failed: {e}",
                        verdict_reason="env_broken",
                    ))
                    continue
                # P0-4: sanity gate 双检（F2P 断言失败 / P2P 通过），结果缓存
                reset_workdir(workdir)
                sanity = _cached_sanity(task, workdir, env)
                if not sanity.ok:
                    results.append(TaskResult(
                        instance_id=instance_id, cell=cell,
                        passed=False, error="sanity gate: " + "; ".join(sanity.reasons),
                        verdict_reason="env_broken",
                    ))
                    continue
                reset_workdir(workdir)

            config = AgentConfig(
                max_turns=50,
                model=model_name,
                concurrent_tools=cell.concurrency,
                permission_mode="auto",
                memory=MemoryConfig(enabled=False),
            )
            config.compression.enabled = cell.compression
            config.repo_map.enabled = cell.repo_map
            config.db_path = _run_db_path(instance_id, cell)

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
                    def stream(self, messages, tools=None, config=None):
                        self.call_count += 1
                        return iter(())
                backend = _FakeBackend()
            else:
                backend = _build_deepseek_backend(model_name)

            try:
                agent = Agent(config, backend)
                traj = agent.run(task["problem_statement"], workdir)
                stats = _extract_stats(traj.config.db_path)
                stats["instance_id"] = instance_id
                result = TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None if use_fake else True,  # fake 不判 pass/fail
                    stats=stats,
                    trajectory_path=traj.config.db_path,
                    run_id=traj.run_id,
                )
                # P0-3 钻空子检测：在 verify 应用 test_patch 之前，先抓 Agent 原始 diff
                # 是否触碰测试文件（否则会被 test_patch 自身污染成假阳性）。
                from eval.metrics import diff_touches_test_files
                agent_touches = diff_touches_test_files(
                    workdir, task.get("test_patch") or "")
                if not use_fake:
                    assert env is not None
                    vr = verify_run(task, workdir, env)
                    result.verified = vr.verified
                    result.f2p_pass = vr.f2p_pass
                    result.p2p_pass = vr.p2p_pass
                    result.verdict_reason = vr.reason
                    result.passed = vr.verified
                # P0.5: 确定性轨迹指标（进流水线）
                from eval.metrics import run_metrics

                try:
                    result.stats["metrics"] = run_metrics(traj.run_id, traj.config.db_path).to_dict()
                    result.stats["touches_test_files"] = agent_touches
                except Exception as e:
                    result.stats["metrics_error"] = str(e)
                results.append(result)
            except Exception as e:
                results.append(TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None, error=str(e),
                ))

    return results

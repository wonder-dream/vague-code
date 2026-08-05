from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
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


# ── 断点续跑 manifest（P0: 480 runs 中断可续，不重跑已完成 cell）────────

MANIFEST_PATH = Path("runs") / "eval" / "manifest.json"


def load_manifest() -> dict[str, dict[str, Any]]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def mark_done(manifest: dict[str, dict[str, Any]], task: dict, cell: EvalCell,
              error: str | None = None, terminal: bool = False) -> None:
    """terminal=True：确定性失败（env_broken/sanity），resume 直接跳过不重试；
    否则标记 failed，最多重试 2 次（防 checkout/网络瞬态失败卡死重试）。"""
    key = f"{task['instance_id']}__{cell_label(cell)}"
    prev = manifest.get(key, {})
    manifest[key] = {
        "status": "done" if terminal else ("failed" if error else "done"),
        "error": error,
        "retries": prev.get("retries", 0) + 1 if (error and not terminal) else 0,
        "finished_at": time.time(),
    }
    save_manifest(manifest)


def _should_skip(manifest: dict[str, dict[str, Any]], key: str) -> bool:
    """断点续跑跳过规则：done 跳过；failed 最多重试 2 次后跳过（防瞬态失败卡死重试）。"""
    entry = manifest.get(key)
    if entry is None:
        return False
    if entry.get("status") == "done":
        return True
    return (entry.get("retries") or 0) >= 2


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


def _venv_lock_sha1(task: dict, venvs_root: str | Path = "eval/.venvs") -> str:
    """venv 依赖冻结文件哈希（sanity 缓存键 + run 元数据共用，#8/#d）。

    换依赖版本 → 哈希变 → sanity cache 自动失效重检，避免旧判别器结果
    挂在新依赖上（可复现性问题两面之一）。
    """
    lock = Path(venvs_root) / venv_key(task) / "requirements.lock"
    if not lock.exists():
        return "nolock"
    return hashlib.sha1(lock.read_bytes()).hexdigest()[:12]


def _cached_sanity(task: dict, workdir: str, env: Any) -> SanityResult:
    """sanity gate 结果按 (repo, base_commit, deps-fingerprint) 缓存，避免每 repeat 重跑。"""
    cache = load_sanity_cache()
    key = f"{venv_key(task)}::{_venv_lock_sha1(task)}"
    if key in cache:
        return SanityResult(ok=cache[key], reasons=[] if cache[key] else ["cached env_broken"])
    res = sanity_gate(task, workdir, env)
    cache[key] = res.ok
    save_sanity_cache(cache)
    return res


def _run_cost(stats: dict[str, Any], price_input: float, price_output: float,
              price_cache: float = 0.07) -> float:
    """单 run 成本估算（USD）。

    DeepSeek 上下文缓存为自动前缀命中（实测 hit 率 81-93%），命中部分按
    cache 单价计（约 1/4 miss 价）；否则 480 runs 的成本会被高估 60%+。
    """
    cached = stats.get("cache_read_tokens", 0)
    fresh = max(stats.get("total_input_tokens", 0) - cached, 0)
    return (fresh / 1e6 * price_input
            + cached / 1e6 * price_cache
            + stats.get("total_output_tokens", 0) / 1e6 * price_output)


def _extract_stats(trajectory_path: str, run_id: str = "") -> dict[str, Any]:
    """从 SQLite trajectory 提取统计指标（按 run_id 过滤——断点续跑/重试时
    同一 db 文件可能累积多个 run 的事件，不过滤会统计翻倍）。"""
    import sqlite3

    try:
        conn = sqlite3.connect(trajectory_path)
        if run_id:
            events = conn.execute(
                "SELECT turn, type, payload FROM events WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        else:
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
        "cache_read_tokens": 0,
        "permission_checks": 0,
        "run_end_reason": "",
        "supervision_calls": 0,
        "supervision_input_tokens": 0,
        "supervision_output_tokens": 0,
        "supervision_cache_read_tokens": 0,
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
            stats["cache_read_tokens"] += usage.get("cache_read_tokens", 0)
        elif etype == "permission_check":
            stats["permission_checks"] += 1
        elif etype == "supervision":
            stats["supervision_calls"] += 1
            usage = payload.get("usage", {})
            stats["supervision_input_tokens"] += usage.get("input_tokens", 0)
            stats["supervision_output_tokens"] += usage.get("output_tokens", 0)
            stats["supervision_cache_read_tokens"] += usage.get("cache_read_tokens", 0)
        elif etype == "run_end":
            stats["run_end_reason"] = payload.get("reason", "")

    return stats


REPO_CACHE = Path("eval") / ".cache" / "repos"


def _git_clone_with_retry(repo_url: str, workdir: str, timeout: int = 300) -> None:
    """git clone 带重试（GitHub 偶发瞬断/锁），失败时把 stderr 尾部带进异常。"""
    last: subprocess.CalledProcessError | None = None
    for attempt in range(3):
        try:
            subprocess.run(
                ["git", "clone", "--filter=blob:none", repo_url, workdir],
                capture_output=True, check=True, timeout=timeout,
            )
            return
        except subprocess.CalledProcessError as e:
            last = e
            if e.stderr:
                tail = e.stderr.decode("utf-8", errors="replace").strip()[-800:]
                if tail:
                    print(f"[clone] attempt {attempt + 1}/3 failed: {tail}")
            time.sleep(5 * (attempt + 1))
    raise last  # type: ignore[misc]


def _archive_workdir(task: dict, workdir: str) -> None:
    """clone 成功后归档到本地缓存（网络瞬断免疫 + 基线 480 runs 免重复 clone）。"""
    import tarfile
    cache_file = REPO_CACHE / f"{venv_key(task)}.tar.gz"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tar.gz.tmp")
    with tarfile.open(tmp, "w:gz") as t:
        t.add(workdir, arcname="")
    tmp.replace(cache_file)


def _force_remove(p: Path) -> None:
    """Windows 上删除目录带重试 + cmd rmdir 兜底（git/杀软可能短暂占用）。"""
    if not p.exists():
        return
    for _ in range(5):
        try:
            shutil.rmtree(p)
            return
        except PermissionError:
            time.sleep(2)
    subprocess.run(
        ["cmd", "/c", "rmdir", "/s", "/q", str(p.resolve())],
        capture_output=True, timeout=120,
    )
    if p.exists():
        raise RuntimeError(f"cannot remove stale workdir: {p}")


def _set_workdir(task: dict, base_dir: str, use_fake: bool = False, cell: EvalCell | None = None) -> str:
    """Clone repo at base_commit into workdir (fake 模式跳过克隆，用临时空目录).

    workdir 按 instance + cell 隔离（U5 修复）：并行评测时不同 cell 互不
    干扰，避免 _force_remove/clone 互删目录导致 checkout failed。
    缓存优先（eval/.cache/repos/<repo>__<commit>.tar.gz）：预热一次后
    480 runs 免重复 clone，且 GitHub 网络抖动免疫。
    """
    repo_url = f"https://github.com/{task['repo']}.git"
    commit = task["base_commit"]
    suffix = f"__{cell_label(cell)}" if cell is not None else ""
    workdir = str(Path(base_dir) / f"{task['instance_id']}{suffix}")

    if use_fake:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        return workdir

    _force_remove(Path(workdir))

    # 本地缓存命中：解压到全新临时目录 → 原子 move（Windows 上"删除后立即解压
    # 同路径"有句柄释放竞态，WinError 267 实证；解压与删除解耦后消除）
    cached = REPO_CACHE / f"{venv_key(task)}.tar.gz"
    if cached.exists():
        import tarfile
        tmp_dir = Path(base_dir) / f".restore_{task['instance_id']}{suffix}"
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with tarfile.open(cached, "r:gz") as t:
                t.extractall(tmp_dir)          # tar 顶层为空名条目，文件直接落 tmp_dir
            if Path(workdir).exists():
                _force_remove(Path(workdir))
            shutil.move(str(tmp_dir), workdir)
            subprocess.run(
                ["git", "checkout", commit],
                cwd=workdir, capture_output=True, check=True, timeout=60,
            )
            return workdir
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"[cache] repo cache restore failed, falling back to clone: {e}")
    _git_clone_with_retry(repo_url, workdir)
    try:
        subprocess.run(
            ["git", "checkout", commit],
            cwd=workdir, capture_output=True, check=True, timeout=60,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"checkout {commit[:12]}: "
                           + (e.stderr.decode("utf-8", errors="replace").strip()[-400:]
                              if e.stderr else "git checkout failed")) from e
    try:
        _archive_workdir(task, workdir)
    except Exception as e:
        print(f"[cache] archive failed (will clone next time): {e}")
    return workdir


def _task_prompt(task: dict) -> str:
    """发任务的 prompt 文本：problem_statement + 验证标准 + 评测环境声明（P0-2b）。

    只改发出去的文本，不改 task 数据（judge/轨迹/verify 不受影响）。
    """
    ps = str(task.get("problem_statement") or "")
    f2p = task.get("FAIL_TO_PASS") or []
    lines = [ps]
    if f2p:
        lines.append("\n验证标准（修复后应通过，可自行运行确认）:")
        lines.extend(f"- pytest {nid}" for nid in f2p)
    lines.append(
        "\n本环境为自动化评测，无交互通道：不要提问等待，陈述假设并继续；"
        "最终交付物为工作区相对 HEAD 的代码改动。"
    )
    return "\n".join(lines)


def run_eval(
    tasks: list[dict],
    matrix: list[EvalCell],
    workdir_base: str,
    use_fake: bool = False,
    model_name: str = "deepseek-v4-flash",
    max_turns: int = 500,
    resume: bool = True,
    max_cost: float | None = None,
    price_input: float = 0.28,
    price_output: float = 1.10,
    price_cache: float = 0.07,
    supervisor: bool = False,
    supervisor_model: str | None = None,
) -> list[TaskResult]:
    from src.agent.loop import Agent
    from src.agent.config import AgentConfig, MemoryConfig, SupervisionConfig
    from src.agent.ir import ModelResponse, NormalizedUsage, StopReason, TextBlock

    results: list[TaskResult] = []
    manifest = load_manifest() if resume else {}
    skipped = 0
    total_cost = 0.0

    for cell in matrix:
        for task in tasks[:1] if use_fake else tasks:
            instance_id = task["instance_id"]
            key = f"{instance_id}__{cell_label(cell)}"

            if resume and _should_skip(manifest, key):
                skipped += 1
                continue

            try:
                workdir = _set_workdir(task, workdir_base, use_fake=use_fake, cell=cell)
            except Exception as e:
                results.append(TaskResult(
                    instance_id=instance_id, cell=cell,
                    passed=None, error=f"checkout failed: {e}",
                ))
                mark_done(manifest, task, cell, error="checkout failed")
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
                    mark_done(manifest, task, cell, error="env_broken", terminal=True)
                    continue
                except Exception as e:
                    results.append(TaskResult(
                        instance_id=instance_id, cell=cell,
                        passed=False, error=f"env setup failed: {e}",
                        verdict_reason="env_broken",
                    ))
                    mark_done(manifest, task, cell, error="env setup failed")
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
                    mark_done(manifest, task, cell, error="sanity gate", terminal=True)
                    continue
                reset_workdir(workdir)

            config = AgentConfig(
                max_turns=max_turns,
                model=model_name,
                concurrent_tools=cell.concurrency,
                permission_mode="auto",
                memory=MemoryConfig(enabled=False),
                supervision=SupervisionConfig(enabled=supervisor, model=supervisor_model),
            )
            config.compression.enabled = cell.compression
            config.repo_map.enabled = cell.repo_map
            config.db_path = _run_db_path(instance_id, cell)

            if use_fake:
                from src.agent.ir import Message, MessageEnd, MessageStart

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
                        # 正常 end_turn 流（空流会让 agent 走 stream_disconnect 死路，
                        # 监督钩子永远不触发，fake 冒烟就测不到监督链路）
                        self.call_count += 1
                        return iter([
                            MessageStart(model=config.get("model", "fake")),
                            MessageEnd(
                                stop_reason=StopReason.end_turn,
                                usage=NormalizedUsage(input_tokens=10, output_tokens=5),
                            ),
                        ])
                backend = _FakeBackend()
            else:
                backend = _build_deepseek_backend(model_name)

            try:
                agent = Agent(config, backend)
                traj = agent.run(_task_prompt(task), workdir)
                stats = _extract_stats(traj.config.db_path, traj.run_id)
                stats["instance_id"] = instance_id
                # #c: 每 run 成本落元数据（cache 命中部分按 cache 单价）
                cost = _run_cost(stats, price_input, price_output, price_cache)
                stats["cost_usd"] = round(cost, 6)
                total_cost += cost
                # 监督成本分列（验收 5：监督增量 < 主 run 成本 15% 的可对比口径）
                sup_fresh = max(stats.get("supervision_input_tokens", 0)
                                - stats.get("supervision_cache_read_tokens", 0), 0)
                stats["supervision_cost_usd"] = round(
                    sup_fresh / 1e6 * price_input
                    + stats.get("supervision_cache_read_tokens", 0) / 1e6 * price_cache
                    + stats.get("supervision_output_tokens", 0) / 1e6 * price_output,
                    6,
                )
                # #8: 依赖冻结指纹进 run 元数据（reproducibility 证据）
                stats["deps_sha1"] = _venv_lock_sha1(task)
                lock = Path("eval") / ".venvs" / venv_key(task) / "requirements.lock"
                stats["deps_path"] = str(lock)
                if lock.exists():
                    stats["deps_count"] = sum(
                        1 for ln in lock.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.lstrip().startswith("#")
                    )
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
            mark_done(manifest, task, cell, error=results[-1].error)

            # #c: 成本熔断——累计超预算立即停，剩余 cell 全跳过
            if max_cost is not None and total_cost >= max_cost:
                remaining = len(matrix) * len(tasks) - len(results) - skipped
                print(f"[cost guard] total_cost=${total_cost:.4f} >= max_cost=${max_cost:.4f}; "
                      f"stopping ({remaining} cells skipped)")
                break
        else:
            continue
        break

    if skipped:
        print(f"[resume] skipped {skipped} already-done cells (manifest: {MANIFEST_PATH})")
    return results

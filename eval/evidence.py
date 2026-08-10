"""证据链三件套 config.json / lock.json / result.json（ADR-0040，对齐审查报告 4.5）。

每次评测运行在 runs/eval/run_<时间戳>/ 下固化：
- config.json：完整运行配置（CLI 参数 + 模型/矩阵/单价等）
- lock.json：任务内容 sha256 + venv 依赖指纹 + 框架版本（锁运行形态，防混用）
- result.json：逐题 reward/异常/重试/成本明细

原则：任何对外宣称的数字都必须能从该目录重建或仲裁。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from eval.matrix import TaskResult


def _task_digest(task: dict) -> str:
    """任务内容 sha256（problem_statement/test_patch/测试清单/基础 commit）。"""
    keys = ("instance_id", "problem_statement", "test_patch", "FAIL_TO_PASS",
            "PASS_TO_PASS", "base_commit")
    digest = hashlib.sha256()
    for key in keys:
        digest.update(str(task.get(key, "")).encode("utf-8", errors="replace"))
    return digest.hexdigest()[:16]


def _version() -> str:
    """框架版本：git HEAD（存在则），否则 vague-code 版本。"""
    try:
        import subprocess
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if head.returncode == 0:
            return head.stdout.strip()
    except Exception:
        pass
    try:
        from importlib.metadata import version
        return version("vague-code")
    except Exception:
        return "unknown"


def _deps_fingerprint(tasks: list[dict]) -> dict[str, str]:
    """每任务 venv 依赖指纹（requirements.lock sha1），无 lock 则空串。"""
    from eval.env import venv_key

    out: dict[str, str] = {}
    seen: set[str] = set()
    for task in tasks:
        key = venv_key(task)
        if key in seen:
            continue
        seen.add(key)
        lock = Path("eval") / ".venvs" / key / "requirements.lock"
        if lock.is_file():
            out[key] = hashlib.sha1(lock.read_bytes()).hexdigest()[:16]  # noqa: S324
    return out


def write_evidence(
    run_dir: str | Path,
    config: dict[str, Any],
    tasks: list[dict],
    results: list[TaskResult],
) -> Path:
    """固化 config/lock/result 三件套到 run 目录，返回目录路径。"""
    out = Path(run_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    lock = {
        "version": _version(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tasks": {t["instance_id"]: _task_digest(t) for t in tasks},
        "deps_sha1": _deps_fingerprint(tasks),
        "task_count": len(tasks),
        "run_count": len(results),
    }
    (out / "lock.json").write_text(
        json.dumps(lock, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    (out / "result.json").write_text(
        json.dumps([r.to_dict() for r in results], indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    return out


def write_report_md(run_dir: str | Path, report_text: str) -> Path:
    """把报告 README（指标/口径/证据索引）写入 run 目录。"""
    out = Path(run_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "README.md"
    path.write_text(report_text, encoding="utf-8")
    return path

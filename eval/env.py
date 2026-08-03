from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── 每 repo 的环境搭建规格（P0-5 审计项：未策展的仓库标 env_broken） ────
# python: uv 按此版本创建 venv（缺失会自动下载）
# install: 依次执行的 pip 参数（`python -m pip install <args>`）
REPO_SETUP: dict[str, dict[str, Any]] = {
    "sympy/sympy": {
        "python": "3.11",
        "install": [["-e", "."], ["pytest", "numpy", "mpmath"]],
    },
    "pylint-dev/pylint": {
        "python": "3.11",
        "install": [["-e", "."], ["pytest", "toml"]],
    },
    # 其余仓库待 audit_tasks 逐仓策展（astropy/matplotlib/sklearn/sphinx 编译重，
    # 缺 install 规格 → ensure_env 抛 EnvNotCurated，对应任务标 env_broken）
}


class EnvNotCurated(FileNotFoundError):
    """仓库没有 install 规格，环境无法搭建。"""


@dataclass
class EnvSpec:
    venv_dir: Path
    python: Path
    repo_key: str
    repo: str

    @property
    def pip(self) -> list[str]:
        return [str(self.python), "-m", "pip"]

    def run(self, cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            check=True, timeout=timeout,
        )


def venv_key(task: dict) -> str:
    repo = str(task.get("repo", "unknown")).replace("/", "__")
    commit = (task.get("base_commit") or "")[:12] or "head"
    return f"{repo}__{commit}"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_env(
    task: dict,
    workdir: str | Path,
    venvs_root: str | Path = "eval/.venvs",
    clear: bool = False,
) -> EnvSpec:
    """为任务提供可复用的 uv venv（缓存于 eval/.venvs/<repo>__<commit>/）。

    位于任务仓库树之外，P0-2 的 git clean -fdx 不会扫到。
    缓存命中以 `.xclaw_ready` 标记为准——install 任一步失败则重建，
    避免"装一半"的 venv 被当成就绪（REPO_SETUP 变更后需 clear）。
    """
    workdir = Path(workdir)
    repo = str(task.get("repo", ""))
    setup = REPO_SETUP.get(repo)
    if not setup:
        raise EnvNotCurated(
            f"repo {repo!r} not curated: add install steps to eval/env.py REPO_SETUP"
        )

    key = venv_key(task)
    venv_dir = (Path(venvs_root) / key).resolve()
    python_bin = _venv_python(venv_dir)
    ready_marker = venv_dir / ".xclaw_ready"

    if not clear and python_bin.exists() and ready_marker.exists():
        return EnvSpec(venv_dir=venv_dir, python=python_bin, repo_key=key, repo=repo)

    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["uv", "venv", str(venv_dir), "--python", setup["python"]],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    for args in setup["install"]:
        _run_pip(python_bin, args, workdir)
    ready_marker.write_text("ok", encoding="utf-8")
    return EnvSpec(venv_dir=venv_dir, python=python_bin, repo_key=key, repo=repo)


def _run_pip(python_bin: Path, args: list[str], cwd: Path) -> None:
    # uv venv 默认不带 pip 模块（uv 0.11+），用 uv pip install --python 直装
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python_bin), "--quiet", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1200,
    )

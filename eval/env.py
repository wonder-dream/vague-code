from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class EnvNotCurated(FileNotFoundError):
    """仓库没有 install 规格，环境无法搭建。"""


def _raise_stub(mod_name: str) -> str:
    """通用桩：import 安全；dunder 探测优雅降级，真实调用报 NotImplementedError。"""
    return (
        f"def __getattr__(name):\n"
        f"    if name.startswith('__'):\n"
        f"        raise AttributeError(name)\n"
        f"    raise NotImplementedError("
        f"'XClaw shim: {mod_name} is a compiled module, "
        f"no MSVC build available on this machine')\n"
    )


_RAISE_STUB = _raise_stub


# ── 每 repo 的环境搭建规格（P0-5 审计项：未策展的仓库标 env_broken） ────
# python: uv 按此版本创建 venv（缺失会自动下载）
# python_by_date: [(起始日期, 版本), ...] 按 base_commit 日期选版本（列表顺序匹配首个）
# install: 依次执行的 pip 参数（`uv pip install --python <venv> <args>`）
#
# 策略：不 editable 安装任务仓库本体（本机无 MSVC，C 扩展源码构建不可行），
# 只装依赖 wheel；跑测试时由 verify 注入 PYTHONPATH=<workdir>，
# 使 `import <repo>` 命中任务 base_commit 源码（纯 Python 测试路径成立）。
# 编译依赖重的测试路径（如 sklearn 全部、astropy.wcs）→ sanity gate 会如实报 env_broken。
REPO_SETUP: dict[str, dict[str, Any]] = {
    "sympy/sympy": {
        "python": "3.9",
        "python_by_date": [
            ("2020-01-01", "3.11"),   # 2020 之后用 3.11；更老的回退 3.9
        ],
        "install": [["pytest", "numpy<2", "scipy", "mpmath"]],
    },
    "pylint-dev/pylint": {
        "python": "3.11",
        "install": [["pytest", "toml"]],
    },
    "pytest-dev/pytest": {
        "python": "3.9",
        "install": [["pluggy", "iniconfig", "packaging", "py", "toml",
                     "importlib-metadata", "attrs", "hypothesis",
                     "atomicwrites", "colorama", "more-itertools"]],
        # _pytest._version 是 setuptools_scm 构建期生成文件，源码树里不存在
        "sysmodules": {
            "_pytest._version": (
                "version = '6.2.2'\n"
                "version_tuple = (6, 2, 2)\n"
            ),
        },
    },
    "astropy/astropy": {
        "python": "3.11",
        "install": [["pytest", "numpy<2", "scipy", "pyerfa", "setuptools_scm",
                     "pytest-doctestplus", "hypothesis", "pytest-astropy"]],
        # 本机无 MSVC，源码构建不可行。astropy 的 import 守卫要求 astropy.utils._compiler
        # 存在（编译产物），且 table/operations 无条件 import _np_utils 等 Cython 模块。
        # sitecustomize 在解释器启动时把桩注入 sys.modules：
        #   - 守卫类桩：提供所需 API（_compiler / _column_mixins 纯 Python 近似）
        #   - 其余 Cython 模块：导入安全，调用即报 NotImplementedError（测试路径真用到才算失败）
        "sysmodules": {
            "astropy.utils._compiler": (
                "def get_compiler():\n"
                "    return 'unknown'\n\n"
                "def has_compiler():\n"
                "    return False\n"
            ),
            "astropy.table._column_mixins": (
                "import numpy as np\n\n"
                "class _ColumnGetitemShim:\n"
                "    def __getitem__(self, item):\n"
                "        out = np.ndarray.__getitem__(self, item)\n"
                "        if isinstance(out, np.ndarray):\n"
                "            try:\n"
                "                return type(self)(out, copy=False)\n"
                "            except Exception:\n"
                "                return out\n"
                "        return out\n\n"
                "class _MaskedColumnGetitemShim(_ColumnGetitemShim):\n"
                "    pass\n"
            ),
            "astropy.table._np_utils": _RAISE_STUB("astropy.table._np_utils"),
            "astropy.io.ascii.cparser": _RAISE_STUB("astropy.io.ascii.cparser"),
            "astropy.io.fits._utils": _RAISE_STUB("astropy.io.fits._utils"),
            "astropy.stats._stats": _RAISE_STUB("astropy.stats._stats"),
            "astropy.convolution._convolve": _RAISE_STUB("astropy.convolution._convolve"),
            "astropy.cosmology.flrw.scalar_inv_efuncs": _RAISE_STUB(
                "astropy.cosmology.flrw.scalar_inv_efuncs"),
            "astropy.timeseries.periodograms.bls._impl": _RAISE_STUB(
                "astropy.timeseries.periodograms.bls._impl"),
            "astropy.timeseries.periodograms.lombscargle.implementations.cython_impl": _RAISE_STUB(
                "astropy.timeseries.periodograms.lombscargle.implementations.cython_impl"),
        },
    },
    "sphinx-doc/sphinx": {
        "python": "3.11",
        "install": [["setuptools<70"],   # 旧版 sphinx 测试依赖 pkg_resources（新 setuptools 已删）
                    ["pytest", "docutils", "Jinja2", "requests", "babel", "alabaster",
                     "imagesize", "snowballstemmer", "packaging", "pygments", "roman",
                     "sphinxcontrib-applehelp", "sphinxcontrib-devhelp",
                     "sphinxcontrib-htmlhelp", "sphinxcontrib-jsmath",
                     "sphinxcontrib-qthelp", "sphinxcontrib-serializinghtml"]],
        # 2021 前的旧 sphinx：Jinja2<3.1（environmentfilter 被删）、docutils<0.18、
        # 旧版 sphinxcontrib-*（新包要求 sphinx>=5）
        "install_by_date": [("2021-06-01", [
            "Jinja2<3.1", "docutils<0.18", "alabaster==0.7.12",
            "sphinxcontrib-applehelp==1.0.2", "sphinxcontrib-devhelp==1.0.2",
            "sphinxcontrib-htmlhelp==2.0.0", "sphinxcontrib-jsmath==1.0.1",
            "sphinxcontrib-qthelp==1.0.3", "sphinxcontrib-serializinghtml==1.1.5",
        ])],
    },
    "scikit-learn/scikit-learn": {
        "python": "3.11",
        "install": [["pytest", "numpy<2", "scipy", "joblib", "threadpoolctl", "pandas"]],
    },
    # 其余仓库待 audit_tasks 逐仓策展（缺 install 规格 → ensure_env 抛 EnvNotCurated）
}


@dataclass
class EnvSpec:
    venv_dir: Path
    python: Path
    repo: str
    shims_dir: Path | None = None

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


def _commit_date(workdir: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(workdir), "log", "-1", "--format=%cs"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _pick_python(setup: dict[str, Any], workdir: Path) -> str:
    """按 base_commit 日期选 Python 版本（python_by_date 列表首个命中）。"""
    rules = setup.get("python_by_date")
    if not rules:
        return str(setup.get("python", "3.11"))
    commit_date = _commit_date(workdir)
    for date, version in rules:
        if commit_date and commit_date >= date:
            return str(version)
    return str(setup.get("python", "3.11"))


def _extra_install(setup: dict[str, Any], workdir: Path) -> list[list[str]]:
    """install_by_date：[(起始日期, pip args)]——提交日期早于阈值的额外安装步骤。"""
    rules = setup.get("install_by_date") or []
    commit_date = _commit_date(workdir)
    return [args for date, args in rules if commit_date and commit_date < date]


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
    lock_file = venv_dir / "requirements.lock"

    if not clear and python_bin.exists() and ready_marker.exists():
        if not lock_file.exists():
            _freeze_lock(python_bin, lock_file)
        return EnvSpec(venv_dir=venv_dir, python=python_bin, repo=repo,
                       shims_dir=_write_shims(venv_dir, setup))

    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv_dir.mkdir(parents=True, exist_ok=True)

    python_ver = _pick_python(setup, workdir)
    subprocess.run(
        ["uv", "venv", str(venv_dir), "--python", python_ver],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    for args in setup["install"]:
        _run_pip(python_bin, args, workdir)
    for args in _extra_install(setup, workdir):
        _run_pip(python_bin, args, workdir)
    _freeze_lock(python_bin, lock_file)
    ready_marker.write_text("ok", encoding="utf-8")
    shims_dir = _write_shims(venv_dir, setup)
    return EnvSpec(venv_dir=venv_dir, python=python_bin, repo=repo,
                   shims_dir=shims_dir)


def _freeze_lock(python_bin: Path, lock_file: Path) -> None:
    """uv pip freeze 落盘 venv 依赖清单（#8：每次 run 的 deps 指纹可追溯）。

    uv venv 无 pip 模块，用 `uv pip freeze --python` 直取；失败不致命
    （缺 lock 时 harness 侧以 'nolock' 指纹兜底）。
    """
    try:
        proc = subprocess.run(
            ["uv", "pip", "freeze", "--python", str(python_bin)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        if proc.returncode == 0:
            lock_file.write_text(proc.stdout, encoding="utf-8")
    except Exception:
        pass


def _write_shims(venv_dir: Path, setup: dict[str, Any]) -> Path | None:
    """把 import 守卫的桩模块写成 shims/sitecustomize.py（venv 缓存目录内）。

    sitecustomize 在解释器启动时自动导入：把桩模块注入 sys.modules，
    使 `from .xxx import _compiler` 类守卫命中桩（不污染任务 workdir，
    P0-2 的 git clean -fdx 与 diff 检测不受影响）。
    """
    sysmodules = setup.get("sysmodules")
    if not sysmodules:
        return None
    shims_dir = venv_dir / "shims"
    shims_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    body = ",\n".join(
        f"{_json.dumps(name)}: {_json.dumps(content)}"
        for name, content in sysmodules.items()
    )
    sitecustomize = (
        "import sys, types\n"
        "MODULES = {\n" + body + "\n}\n"
        "for _name, _code in MODULES.items():\n"
        "    if _name not in sys.modules:\n"
        "        _m = types.ModuleType(_name)\n"
        "        _m.__package__ = _name.rpartition('.')[0]\n"
        "        exec(_code, _m.__dict__)\n"
        "        sys.modules[_name] = _m\n"
    )
    (shims_dir / "sitecustomize.py").write_text(sitecustomize, encoding="utf-8")
    return shims_dir


def _run_pip(python_bin: Path, args: list[str], cwd: Path) -> None:
    # uv venv 默认不带 pip 模块（uv 0.11+），用 uv pip install --python 直装
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python_bin), "--quiet", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1200,
    )

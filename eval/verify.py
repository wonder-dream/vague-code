from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from eval.env import EnvSpec


# ── 结果类型 ────────────────────────────────────────────────────────────

@dataclass
class VerifyResult:
    verified: bool
    f2p_pass: bool | None
    p2p_pass: bool | None
    reason: str
    stdout: str = ""


@dataclass
class SanityResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


# ── P0-1/2: 工具函数 ────────────────────────────────────────────────────

def reset_workdir(workdir: str | Path) -> None:
    """P0-2 状态隔离：把任务仓库恢复到 HEAD 的逐字节一致状态。

    仅在任务 workdir 内执行；eval/.venvs、eval/runs 位于此树之外，不受影响。
    非 git 目录（如 fake 模式的临时空目录）直接跳过。
    """
    workdir = Path(workdir)
    if not (workdir / ".git").exists():
        return
    _git(workdir, ["restore", "."])
    _git(workdir, ["clean", "-fdx"])


def diff_empty(workdir: str | Path) -> bool:
    """工作区相对 HEAD 无任何改动（含未跟踪文件）。"""
    workdir = Path(workdir)
    if not (workdir / ".git").exists():
        return True
    staged = _git(workdir, ["diff", "--quiet", "HEAD"], check=False)
    untracked = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return staged.returncode == 0 and not untracked.stdout.strip()


def parse_test_paths(test_patch: str) -> list[str]:
    """从 test_patch 的 `diff --git a/X b/Y` 行提取文件路径（P0-3 回滚用）。"""
    paths: list[str] = []
    for line in test_patch.splitlines():
        m = re.match(r"^diff --git a/\S+ b/(\S+)$", line)
        if m:
            paths.append(m.group(1))
    return paths


def reset_test_files(workdir: str | Path, paths: list[str]) -> None:
    """把 test_patch 覆盖的测试文件回滚到 base（P0-3）。

    解决两个问题：Agent 篡改测试文件（钻空子）；Agent 与测试文件冲突导致
    git apply 失败误判 fail。
    """
    if paths:
        _git(Path(workdir), ["checkout", "--", *paths])


def apply_test_patch(workdir: str | Path, test_patch: str) -> None:
    if not test_patch.strip():
        return
    workdir = Path(workdir)
    patch_file = workdir / ".xclaw_test_patch.diff"
    patch_file.write_text(test_patch, encoding="utf-8")
    try:
        # --ignore-whitespace 吸收 Windows 上 autocrlf 造成的 CRLF/LF 差异
        _git(workdir, ["apply", "--ignore-whitespace", "--whitespace=fix", str(patch_file.resolve())])
    finally:
        patch_file.unlink(missing_ok=True)


# ── P0-1: pytest 执行与结果分类 ─────────────────────────────────────────

class TestRun:
    def __init__(self, node_id: str, state: str, exit_code: int, output: str):
        self.node_id = node_id
        self.state = state          # pass | fail | collection_error | no_tests | timeout
        self.exit_code = exit_code
        self.output = output


def classify_pytest(exit_code: int, output: str) -> str:
    """区分断言失败 vs collection/import error（P0-4 关键）。

    pass | fail | collection_error | no_tests
    """
    # python -m <缺模块> 只打印 "No module named pytest"（无 ModuleNotFoundError 前缀）
    if ("No module named" in output
            or "ModuleNotFoundError" in output
            or "ImportError" in output):
        return "collection_error"
    if "error collecting" in output or "ERROR collecting" in output:
        return "collection_error"
    if "no tests ran" in output:
        return "no_tests"
    if "failed" in output and exit_code != 0:
        return "fail"
    if exit_code == 0:
        return "pass"
    if exit_code in (2, 3, 4, 5):
        return "collection_error"
    return "fail"


def run_node_ids(
    env: EnvSpec,
    workdir: str | Path,
    node_ids: list[str],
    timeout_s: int = 600,
) -> list[TestRun]:
    """逐个 node id 跑 pytest，独立分类（SWE-bench 惯例：只跑指定 node id）。"""
    workdir = Path(workdir)
    runs: list[TestRun] = []
    for node_id in node_ids:
        try:
            proc = subprocess.run(
                [str(env.python), "-m", "pytest", node_id, "-q",
                 "--no-header", "-p", "no:cacheprovider"],
                cwd=str(workdir), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout_s,
            )
            out = proc.stdout + "\n" + proc.stderr
            state = classify_pytest(proc.returncode, out)
            runs.append(TestRun(node_id, state, proc.returncode, out))
        except subprocess.TimeoutExpired:
            runs.append(TestRun(node_id, "timeout", -1, "timeout exceeded"))
    return runs


# ── P0-4: sanity gate 双检 ──────────────────────────────────────────────

def sanity_gate(task: dict, workdir: str | Path, env: EnvSpec, timeout_s: int = 300) -> SanityResult:
    """在干净 checkout 上校验环境与测试判别器。

    - F2P 必须**断言失败**（collection/import error 视为环境没搭好）
    - P2P 必须通过（否则环境错误会让所有 run 集体假 fail）
    调用方负责先 reset_workdir 保证干净，并事后恢复。
    """
    workdir = Path(workdir)
    apply_test_patch(workdir, task.get("test_patch") or "")

    reasons: list[str] = []
    for node_id in task.get("FAIL_TO_PASS", []):
        run = run_node_ids(env, workdir, [node_id], timeout_s)[0]
        if run.state != "fail":
            reasons.append(
                f"F2P {node_id}: expected assertion-fail on clean checkout, got '{run.state}'"
            )
    for node_id in task.get("PASS_TO_PASS", []):
        run = run_node_ids(env, workdir, [node_id], timeout_s)[0]
        if run.state != "pass":
            reasons.append(f"P2P {node_id}: expected pass on clean checkout, got '{run.state}'")

    return SanityResult(ok=not reasons, reasons=reasons)


# ── P0-1: verify（Agent run 后调用） ────────────────────────────────────

def verify_run(
    task: dict,
    workdir: str | Path,
    env: EnvSpec,
    timeout_s: int = 600,
) -> VerifyResult:
    """验收：git diff 非空 → 回滚测试文件 → apply test_patch → F2P/P2P 全过。

    分工（metrics.py 同款注释）：本函数判 outcome（有没有产出 + 测试过不过）；
    轨迹层面的"有没有验证行为"由 eval/metrics.py 的编辑→验证循环判。
    """
    workdir = Path(workdir)

    if diff_empty(workdir):
        return VerifyResult(verified=False, f2p_pass=False, p2p_pass=False,
                            reason="no_diff")

    reset_test_files(workdir, parse_test_paths(task.get("test_patch") or ""))
    apply_test_patch(workdir, task.get("test_patch") or "")

    f2p_runs = run_node_ids(env, workdir, task.get("FAIL_TO_PASS", []), timeout_s)
    f2p_ok = all(r.state == "pass" for r in f2p_runs)
    if not f2p_ok:
        bad = next(r for r in f2p_runs if r.state != "pass")
        return VerifyResult(verified=False, f2p_pass=False, p2p_pass=None,
                            reason=f"f2p:{bad.state}", stdout=bad.output)

    p2p_runs = run_node_ids(env, workdir, task.get("PASS_TO_PASS", []), timeout_s)
    p2p_ok = all(r.state == "pass" for r in p2p_runs)
    if not p2p_ok:
        bad = next(r for r in p2p_runs if r.state != "pass")
        return VerifyResult(verified=True, f2p_pass=True, p2p_pass=False,
                            reason=f"p2p:{bad.state}", stdout=bad.output)

    return VerifyResult(verified=True, f2p_pass=True, p2p_pass=True, reason="ok")


# ── sanity 缓存 ─────────────────────────────────────────────────────────

def _sanity_cache_path() -> Path:
    return Path("eval") / ".sanity_cache.json"


def load_sanity_cache() -> dict[str, bool]:
    p = _sanity_cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_sanity_cache(cache: dict[str, bool]) -> None:
    _sanity_cache_path().write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _git(workdir: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(workdir), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )

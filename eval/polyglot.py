"""Aider Polyglot 评测运行器（ADR-0040，对齐审查报告 2.1/4.x）。

数据集：https://github.com/Aider-AI/polyglot-benchmark（Exercism 风格"读既有代码
+ 保持接口 + 实现 + 独立测试通过"，6 语言 225 题）。

设计：
- Agent 在宿主跑（vague-code 库 + benchmark 反作弊提示词），工作目录 = 干净任务副本
- verifier 在容器（vague-eval 镜像）内执行，隔离不可信代码；exit 0 = verified
- 状态隔离：每次 run 前重拷任务副本（防残留），等价 SWE 版 reset_workdir
- TaskResult/报告/证据链与 SWE 版复用（pass@1/e2e/pass^k/分类/成本分位）

用法：
    python -m eval.polyglot --dataset <polyglot-benchmark 路径> --model deepseek-v4-flash \
        --repeat 3 --out report.md [--instances python/affine-cipher --fake]
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from eval.matrix import EvalCell, TaskResult

# 每语言容器 verifier 命令（容器内 cwd = 任务目录）
_LANG_VERIFIER: dict[str, list[str]] = {
    "python": ["python3", "-m", "pytest", "-q"],
    "go": ["go", "test", "./..."],
    "rust": ["cargo", "test", "--quiet"],
    "javascript": ["sh", "-c", "npm install --silent --no-audit --no-fund >/dev/null 2>&1 && npm test --silent"],
    # Gradle 不读 HTTP_PROXY 环境变量（审查报告 4.6 明列的坑）→ JVM 系统属性透传代理
    "java": ["sh", "-c",
             "export GRADLE_OPTS=\"-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=7897 "
             "-Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=7897\" "
             "&& ./gradlew --console=plain test >/dev/null 2>&1"],
}

_IMAGE = "vague-eval"
_WSL_DISTRO = "Ubuntu-22.04"

# 数据集目录 → 语言名
_LANGS = ("cpp", "go", "java", "javascript", "python", "rust")


def load_polyglot_tasks(dataset_dir: str | Path) -> list[dict]:
    """扫描数据集，构造任务 dict（题目描述 = instructions.md + append）。"""
    root = Path(dataset_dir)
    tasks: list[dict] = []
    for lang in _LANGS:
        practice = root / lang / "exercises" / "practice"
        if not practice.is_dir():
            continue
        for exercise in sorted(p for p in practice.iterdir() if p.is_dir()):
            inst = f"{lang}/{exercise.name}"
            docs = exercise / ".docs"
            statement_parts = []
            for name in ("instructions.md", "instructions.append.md"):
                f = docs / name
                if f.is_file():
                    statement_parts.append(f.read_text(encoding="utf-8"))
            statement = "\n\n".join(p for p in statement_parts if p.strip())
            tasks.append({
                "instance_id": inst,
                "language": lang,
                "exercise": exercise.name,
                "problem_statement": statement or f"实现 {lang}/{exercise.name} 的全部功能",
                "source_dir": str(exercise),
            })
    return tasks


def _to_wsl_path(path: Path) -> str:
    """Windows 路径 → WSL 挂载路径（/mnt/d/...）。"""
    p = str(path.resolve()).replace("\\", "/")
    return "/mnt/" + p[0].lower() + p[2:]


def _docker(args: list[str], timeout_s: int = 900) -> subprocess.CompletedProcess:
    """经 WSL 调用 dockerd（Windows 侧无 docker CLI）。"""
    cmd = ["wsl", "-d", _WSL_DISTRO, "-u", "root", "--", "docker", *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s,
        encoding="utf-8", errors="replace",
    )


def prepare_task(tasks_root: Path, task: dict) -> Path:
    """复制干净任务副本（排除 .meta/.docs/.approaches 等答案与题目源）。"""
    dest = tasks_root / task["instance_id"].replace("/", "__")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(task["source_dir"], dest)
    # 删答案/题目源（.meta/.docs/.approaches）；保留 gradle wrapper（构建必需）
    for name in (".meta", ".docs", ".approaches", ".github"):
        p = dest / name
        if p.exists():
            shutil.rmtree(p)
    # Windows git autocrlf 会把 gradlew 转成 CRLF → shebang 失效（127 not found）
    _fix_shebang_line_endings(dest)
    return dest


def _fix_shebang_line_endings(task_dir: Path) -> None:
    """把 Unix 脚本（gradlew 等）行尾转回 LF，修复 CRLF shebang 不可执行。"""
    for name in ("gradlew",):
        p = task_dir / name
        if p.is_file():
            data = p.read_bytes()
            if b"\r\n" in data:
                p.write_bytes(data.replace(b"\r\n", b"\n"))


def _cpp_verifier(task: dict) -> list[str]:
    # CMakeLists 的 target 名 = 目录名（保留连字符，如 all-your-base → build/all-your-base）
    exe = task["exercise"]
    return ["sh", "-c",
            f"cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug >/dev/null 2>&1 "
            f"&& cmake --build build >/dev/null 2>&1 && ./build/{exe}"]


# 各语言测试文件定位（从数据集源目录恢复，防 agent 改测试作弊，对齐 SWE 版 P0-3）
_TEST_PATTERNS: dict[str, tuple[str, ...]] = {
    "python": ("*_test.py",),
    "go": ("*_test.go",),
    "javascript": ("*.spec.js",),
    "java": ("src/test",),
    "rust": ("tests",),
    "cpp": ("*_test.cpp", "test"),
}


def _restore_tests(task: dict, workdir: Path) -> None:
    """verify 前把源目录的测试文件覆盖回任务副本（agent 改测试 = 无效）。"""
    source = Path(task["source_dir"])
    patterns = _TEST_PATTERNS.get(task["language"], ())
    for pat in patterns:
        matches = list(source.glob(pat)) if "*" in pat else [source / pat]
        for src in matches:
            if not src.is_file() and not src.is_dir():
                continue
            dest = workdir / src.name if src.parent == source else workdir / src.relative_to(source)
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)


def verify_in_container(task: dict, workdir: Path) -> tuple[bool, str, str]:
    """容器内跑 verifier：返回 (verified, verdict_reason, 输出尾部)。

    挂载点 = exercise 名目录（cpp CMakeLists 用目录名当目标名，/workspace 会失效）。
    """
    _restore_tests(task, workdir)
    if task["language"] == "cpp":
        cmd = _cpp_verifier(task)
    else:
        cmd = _LANG_VERIFIER[task["language"]]
    wsl_workdir = _to_wsl_path(workdir)
    mount_dir = task["exercise"]
    # 语言依赖缓存挂载（WSL 侧持久目录）：npm/gradle 下载一次后命中缓存，避免每题重装
    extra_mounts: list[str] = []
    if task["language"] == "javascript":
        extra_mounts = ["-v", "/root/npm-cache:/root/.npm"]
    elif task["language"] == "java":
        extra_mounts = ["-v", "/root/gradle-cache:/root/.gradle"]
    try:
        proc = _docker([
            "run", "--rm", "--network", "host",
            "-e", "HTTP_PROXY=http://127.0.0.1:7897",
            "-e", "HTTPS_PROXY=http://127.0.0.1:7897",
            *extra_mounts,
            "-v", f"{wsl_workdir}:/{mount_dir}",
            "-w", f"/{mount_dir}",
            _IMAGE, *cmd,
        ])
    except subprocess.TimeoutExpired:
        return False, "verify:timeout", "verifier timed out"
    tail = (proc.stdout + proc.stderr)[-800:]
    ok = proc.returncode == 0
    reason = "verify:pass" if ok else f"verify:fail(exit {proc.returncode})"
    return ok, reason, tail


def run_polyglot_eval(
    tasks: list[dict],
    *,
    model_name: str = "deepseek-v4-flash",
    max_turns: int = 60,
    repeat: int = 1,
    use_fake: bool = False,
    tasks_root: str = "runs/polyglot",
) -> list[TaskResult]:
    """跑 Polyglot 评测：Agent 宿主执行 → 容器 verify → TaskResult。"""
    from vague_code.agent.config import AgentConfig, MemoryConfig
    from vague_code.agent.context import benchmark_identity
    from vague_code.agent.loop import Agent

    root = Path(tasks_root)
    root.mkdir(parents=True, exist_ok=True)
    results: list[TaskResult] = []

    for task in tasks:
        for rep in range(repeat):
            instance = task["instance_id"]
            print(f"[polyglot] {instance} rep={rep}/{repeat}", flush=True)
            workdir = prepare_task(root, task)

            if use_fake:
                from vague_code.agent.ir import (
                    Message, MessageEnd, MessageStart, ModelResponse,
                    NormalizedUsage, StopReason, TextBlock,
                )

                class _FakeBackend:
                    def complete(self, messages, tools=None, config=None) -> ModelResponse:
                        return ModelResponse(
                            message=Message(role="assistant", content=[TextBlock(text="ok")]),
                            stop_reason=StopReason.end_turn,
                            usage=NormalizedUsage(input_tokens=10, output_tokens=5),
                        )

                    def stream(self, messages, tools=None, config=None):
                        return iter([
                            MessageStart(model="fake"),
                            MessageEnd(stop_reason=StopReason.end_turn,
                                       usage=NormalizedUsage(input_tokens=10, output_tokens=5)),
                        ])
                backend = _FakeBackend()
            else:
                from vague_code.cli import _provider_settings, _resolve_api_key
                from vague_code.config import build_backend
                provider = "deepseek"
                base_url, key_env, protocol = _provider_settings(provider, None, None, {})
                api_key = _resolve_api_key(key_env)
                if not api_key:
                    raise SystemExit(f"{key_env} not found; set in .env or environment")
                backend = build_backend(provider, api_key, base_url, protocol, 120.0)

            config = AgentConfig(
                model=model_name, max_turns=max_turns,
                permission_mode="auto",
                memory=MemoryConfig(enabled=False),
                db_path=str(root / f"{instance.replace('/', '__')}__r{rep}.db"),
            )
            config.repo_map.enabled = False  # 单文件题目，符号地图无收益
            from vague_code.agent.permission import Decision

            agent = Agent(config, backend)
            agent._on_permission = lambda op, decision: Decision.ALLOW
            handle = agent.start(task=task["problem_statement"], workdir=str(workdir),
                                 identity=benchmark_identity())
            for _ in handle:
                pass
            traj = handle.trajectory

            verified, reason, tail = verify_in_container(task, workdir)
            usage = _usage_stats(traj)
            stats = {
                **usage,
                "cost_usd": round(
                    usage["total_input_tokens"] / 1e6 * 0.28
                    + usage["total_output_tokens"] / 1e6 * 1.10,
                    6,
                ),
                "verifier_tail": tail,
            }
            results.append(TaskResult(
                instance_id=instance, cell=EvalCell(True, True, True, rep),
                passed=verified, verified=verified, verdict_reason=reason,
                stats=stats, trajectory_path=traj.config.db_path, run_id=traj.run_id,
            ))
            print(f"[polyglot] {instance} rep={rep} -> {reason}", flush=True)
    return results


def _run_end_reason(traj) -> str:
    for e in traj.events:
        if e.type == "run_end":
            return str(e.payload.get("reason", "?"))
    return "?"


def _usage_stats(traj) -> dict:
    """从轨迹 llm_response 事件汇总 token 统计（对齐 harness stats 字段）。"""
    turns = 0
    inp = 0
    outp = 0
    cache = 0
    reason = ""
    for e in traj.events:
        if e.type == "run_end":
            reason = str(e.payload.get("reason", "?"))
        elif e.type == "llm_response":
            raw_usage = e.payload.get("usage")
            if isinstance(raw_usage, dict):
                usage: dict[str, Any] = raw_usage
                inp += int(usage.get("input_tokens") or 0)
                outp += int(usage.get("output_tokens") or 0)
                cache += int(usage.get("cache_read_tokens") or 0)
        elif e.type == "turn_start":
            turns += 1
    return {
        "total_turns": turns,
        "total_input_tokens": inp,
        "total_output_tokens": outp,
        "cache_read_tokens": cache,
        "run_end_reason": reason,
    }


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(prog="python -m eval.polyglot",
                                description="Aider Polyglot eval runner (ADR-0040)")
    p.add_argument("--dataset", required=True, help="polyglot-benchmark checkout path")
    p.add_argument("--out", default="polyglot_report.md", help="Report path")
    p.add_argument("--model", default="deepseek-v4-flash")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--fake", action="store_true", help="FakeBackend smoke")
    p.add_argument("--instances", default=None, help="Comma-separated instance filter")
    p.add_argument("--tasks-root", default="runs/polyglot")
    args = p.parse_args()

    tasks = load_polyglot_tasks(args.dataset)
    if args.instances:
        wanted = {s.strip() for s in args.instances.split(",") if s.strip()}
        tasks = [t for t in tasks if any(
            t["instance_id"] == w or t["instance_id"].startswith(w)
            for w in wanted
        )]
    print(f"Loaded {len(tasks)} polyglot tasks")
    if args.fake:
        tasks = tasks[:1]

    results = run_polyglot_eval(
        tasks, model_name=args.model, max_turns=args.max_turns,
        repeat=args.repeat, use_fake=args.fake, tasks_root=args.tasks_root,
    )

    from eval.evidence import write_evidence
    from eval.reporter import generate_report

    run_dir = Path("runs/eval") / f"polyglot_{time.strftime('%Y%m%d-%H%M%S')}"
    write_evidence(run_dir, {"args": vars(args), "dataset": args.dataset}, tasks, results)
    generate_report(results, args.out)
    print(f"Report: {args.out} | evidence: {run_dir}")


if __name__ == "__main__":
    main()

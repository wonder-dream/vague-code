"""记忆写入前校验器（ADR-0021 机制 1）：规则化事实校验。

纯规则、无 LLM、无外部服务。对"声明性事实"（技术栈/文件路径）在 workdir 内
做证据核对，输出 verified / unverified / contradicted 三值判定。

设计取舍：
- 只校验可机械验证的声明（技术栈、路径），不做语义理解；
- 证据来自依赖清单（pyproject.toml / requirements / package.json ...）
  与源码 import，全部本地 grep，零网络；
- 默认 warn 模式由调用方决定：本模块只负责判定，不负责写入策略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 依赖清单文件名（声明性事实最常见的证据源）
_DEP_MANIFESTS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "Pipfile",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
)

# 源码文件后缀（import 证据源）
_SRC_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java"}

# 技术栈声明 → 证据子串（出现在依赖清单或源码 import 中即算命中）
_DB_STACK_EVIDENCE: dict[str, tuple[str, ...]] = {
    "MySQL": ("mysql", "pymysql", "mysqlclient", "mysqldb"),
    "PostgreSQL": ("postgres", "psycopg", "asyncpg", "pg8000"),
    "SQLite": ("sqlite", "aiosqlite"),
    "Redis": ("redis", "aioredis"),
    "MongoDB": ("pymongo", "motor", "mongodb", "mongoengine"),
    "DynamoDB": ("boto3", "dynamodb"),
}

# 文件/路径声明模式：`文件[:：] xxx.ext` / `路径[:：] xxx.ext` / `file/path: xxx.ext`
_PATH_PATTERN = re.compile(
    r"(?:文件|路径|file|path)[:：]?\s*([\w./\\-]+\.\w+)", re.IGNORECASE
)

_MAX_SCAN_FILES = 300


@dataclass
class FactCheckResult:
    level: str  # "verified" | "unverified" | "contradicted"
    evidence: list[str] = field(default_factory=list)
    rule: str | None = None


class MemoryValidator:
    """规则化事实校验器：针对声明性事实做 repo 证据核对。"""

    def __init__(self, workdir: str):
        self._workdir = Path(workdir)

    # ── 公开 API ──────────────────────────────────────────────────────────

    def check(self, content: str) -> FactCheckResult:
        """对一条记忆内容做全部规则校验。

        - 命中任一规则且证据存在 → verified
        - 命中规则但无证据 → unverified
        - 命中规则且有反证（如声明 MySQL 但代码是 SQLite）→ contradicted
        - 未命中任何规则 → verified（不适用规则，放行）
        """
        results: list[FactCheckResult] = []
        results.append(self._check_db_stack(content))
        results.append(self._check_path(content))
        results = [r for r in results if r is not None]

        if not results:
            return FactCheckResult(level="verified", evidence=[], rule=None)
        if any(r.level == "contradicted" for r in results):
            evidence = [e for r in results for e in r.evidence]
            return FactCheckResult(level="contradicted", evidence=evidence,
                                   rule=next(r.rule for r in results if r.level == "contradicted"))
        if all(r.level == "verified" for r in results):
            evidence = [e for r in results for e in r.evidence]
            return FactCheckResult(level="verified", evidence=evidence,
                                   rule=results[0].rule)
        return FactCheckResult(level="unverified",
                               evidence=[e for r in results for e in r.evidence],
                               rule=results[0].rule)

    # ── 规则实现 ──────────────────────────────────────────────────────────

    def _check_db_stack(self, content: str) -> FactCheckResult | None:
        claimed = [s for s in _DB_STACK_EVIDENCE if re.search(s, content, re.IGNORECASE)]
        if not claimed:
            return None
        repo_text = self._gather_repo_text()
        found = self._find_stacks(repo_text)
        if any(s in found for s in claimed):
            return FactCheckResult(
                level="verified",
                evidence=[f"{s} evidence" for s in claimed if s in found],
                rule="db_stack",
            )
        if found:
            return FactCheckResult(
                level="contradicted",
                evidence=[f"repo uses {s}" for s in sorted(found)],
                rule="db_stack",
            )
        return FactCheckResult(level="unverified", evidence=[], rule="db_stack")

    def _check_path(self, content: str) -> FactCheckResult | None:
        m = _PATH_PATTERN.search(content)
        if not m:
            return None
        fname = m.group(1)
        if (self._workdir / fname).is_file():
            return FactCheckResult(
                level="verified", evidence=[f"{fname} exists"], rule="path",
            )
        return FactCheckResult(level="unverified", evidence=[], rule="path")

    # ── 证据收集 ──────────────────────────────────────────────────────────

    def _gather_repo_text(self) -> list[tuple[str, str]]:
        """收集依赖清单 + 源码 import 的文本（小写），带来源路径。"""
        texts: list[tuple[str, str]] = []
        for name in _DEP_MANIFESTS:
            p = self._workdir / name
            if p.is_file():
                self._append_text(texts, p)
        scanned = 0
        try:
            for p in self._workdir.rglob("*"):
                if scanned >= _MAX_SCAN_FILES:
                    break
                if p.is_file() and p.suffix in _SRC_SUFFIXES:
                    self._append_text(texts, p)
                    scanned += 1
        except OSError:
            pass
        return texts

    def _append_text(self, texts: list[tuple[str, str]], p: Path) -> None:
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            return
        if raw:
            texts.append((str(p), raw))

    def _find_stacks(self, repo_text: list[tuple[str, str]]) -> set[str]:
        found: set[str] = set()
        for stack, needles in _DB_STACK_EVIDENCE.items():
            for _, text in repo_text:
                if any(needle in text for needle in needles):
                    found.add(stack)
                    break
        return found

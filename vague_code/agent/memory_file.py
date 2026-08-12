"""文件式记忆（ADR-0014 更新）：按 workdir 隔离的 markdown 分块文件。

对齐 Claude Code auto memory 的最简形态：`<workdir>/.agent/memory.md`，
每个 `## 标题` 块是一条记忆（含来源 run_id 与时间/hash 注释），文件可人工编辑。
系统提示词注入文件全文，限 200 行 / 25KB（Claude MEMORY.md 同款上限）。

写入幂等：以内容 sha256 前 12 位作为 hash 注释，重复内容不重复写。
进程内按路径加锁串行化写（多会话同 workdir 并发蒸馏安全；多进程同 workdir
并发不在支持范围——eval 禁用记忆，CLI 单任务）。
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_LINES = 200
MAX_BYTES = 25 * 1024

_HEADER = "<!-- vague-code memory: agent 蒸馏的历史会话记忆，可手动编辑 -->"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


class MemoryFile:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        with _locks_guard:
            key = str(self._path)
            if key not in _locks:
                _locks[key] = threading.Lock()
        self._lock = _locks[key]

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> str:
        """读取全文（文件不存在/不可读返回空串）。"""
        if not self._path.is_file():
            return ""
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def inject_text(self) -> str:
        """注入文本：全文截尾（限 200 行 / 25KB，字节截断保持 UTF-8 安全）。"""
        text = self.read().strip()
        if not text:
            return ""
        lines = text.splitlines()
        if len(lines) > MAX_LINES:
            text = "\n".join(lines[:MAX_LINES])
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            text = encoded[:MAX_BYTES].decode("utf-8", errors="ignore")
        return text

    def append(self, title: str, content: str, source_session: str | None = None) -> bool:
        """追加一块记忆；内容 hash 已存在则跳过（幂等）。"""
        content = content.strip()
        title = (title or content[:40]).strip()
        if not content:
            return False
        digest = _content_hash(content)
        with self._lock:
            existing = self.read()
            if f"hash: {digest}" in existing:
                return False
            self._path.parent.mkdir(parents=True, exist_ok=True)
            block = f"## {title}\n<!-- source: {source_session or ''}; created: {_now()}; hash: {digest} -->\n{content}\n"
            if not existing:
                text = _HEADER + "\n\n" + block
            else:
                text = existing.rstrip("\n") + "\n\n" + block
            self._path.write_text(text, encoding="utf-8")
            return True

    def remove_sections(self, source_session: str) -> int:
        """移除来源会话为该 run_id 的所有分块，返回移除数。"""
        if not source_session:
            return 0
        with self._lock:
            text = self.read()
            if not text:
                return 0
            lines = text.splitlines()
            kept: list[str] = []
            removed = 0
            pending_title: str | None = None
            in_block = False
            block_is_target = False
            for line in lines:
                if line.startswith("## "):
                    if in_block:
                        if block_is_target:
                            removed += 1
                        else:
                            kept.append(pending_title or "")
                    pending_title = line
                    block_is_target = False
                    in_block = True
                elif in_block:
                    stripped = line.strip()
                    if stripped.startswith("<!-- source:"):
                        block_is_target = "source: {0};".format(source_session) in stripped
                    if not block_is_target:
                        kept.append(line)
                else:
                    kept.append(line)
            if in_block:
                if block_is_target:
                    removed += 1
                else:
                    kept.append(pending_title or "")
            if removed == 0:
                return 0
            text = "\n".join(kept).rstrip() + "\n"
            self._path.write_text(text, encoding="utf-8")
            return removed

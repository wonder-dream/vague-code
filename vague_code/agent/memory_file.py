"""文件式记忆（ADR-0014 更新）：按 workdir 隔离的 markdown 分块文件。

对齐 Claude Code auto memory 的最简形态：`<workdir>/.agent/memory.md`，
每个 `## 标题` 块是一条记忆（含来源 run_id 与时间/hash 注释），文件可人工编辑。
系统提示词注入文件全文，限 200 行 / 25KB（Claude MEMORY.md 同款上限）。

写入幂等：以内容 sha256 前 12 位作为 hash 注释，重复内容不重复写。
进程内按路径加锁串行化写（多会话同 workdir 并发蒸馏安全；多进程同 workdir
并发不在支持范围——eval 禁用记忆，CLI 单任务）。

ADR-0021（记忆卫生）扩展：修订/作废/按标题/关键词清理，全部共用块解析内核。
"""

from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from vague_code.agent.trust import mark_untrusted

MAX_LINES = 200
MAX_BYTES = 25 * 1024

_HEADER = "<!-- vague-code memory: agent 蒸馏的历史会话记忆，可手动编辑 -->"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

_TITLE_MARK_RE = re.compile(r"^(?:~~\s*|⚠\s*\w+\s*|\[[^\]]+\]\s*)+")
_TITLE_TRAIL_RE = re.compile(r"\s*~~$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def _strip_title_marks(title: str) -> str:
    """去掉标题上的标记（作废 ~~、未验证 ⚠、可能矛盾 [..]），用于匹配。"""
    t = _TITLE_MARK_RE.sub("", title)
    t = _TITLE_TRAIL_RE.sub("", t)
    return t.strip()


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
        """注入文本：全文截尾（限 200 行 / 25KB，字节截断保持 UTF-8 安全）。

        先标注为不可信外部数据（B5），再按最终注入串截尾，保证标记本身也计入限额。
        """
        text = self.read().strip()
        if not text:
            return ""
        text = mark_untrusted(text, "历史蒸馏记忆")
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

    def list_sections(self) -> list[dict]:
        """列出全部块元信息（标题/来源/时间/hash/正文），供展示与人工清理。"""
        _preamble, blocks = self._read_blocks()
        return [
            {
                "title": b["title"],
                "source": b["source"],
                "created": b["created"],
                "hash": b["hash"],
                "body": "\n".join(b["body_lines"]).strip(),
            }
            for b in blocks
        ]

    def replace(
        self, old_title: str, title: str, content: str,
        source_session: str | None = None,
    ) -> bool:
        """精确匹配 old_title 块，替换其标题与内容（ADR-0021 机制 3）。

        标题匹配会忽略已有标记（~~ / ⚠ / [..]）。未命中返回 False，调用方
        可降级为普通 append。
        """
        content = content.strip()
        title = (title or content[:40]).strip()
        if not content:
            return False
        digest = _content_hash(content)
        with self._lock:
            preamble, blocks = self._read_blocks()
            target = next(
                (b for b in blocks if _strip_title_marks(b["title"]) == old_title),
                None,
            )
            if target is None:
                return False
            new_block = {
                "title": title,
                "source": source_session or target.get("source"),
                "created": _now(),
                "hash": digest,
                "body_lines": content.splitlines(),
            }
            blocks[blocks.index(target)] = new_block
            self._write_blocks(preamble, blocks)
            return True

    def deprecate(
        self, title: str, reason: str = "", source_session: str | None = None,
    ) -> bool:
        """把旧条目标记为作废（ADR-0021 机制 3）：保留可见但加 [stale] 标记。"""
        with self._lock:
            preamble, blocks = self._read_blocks()
            target = next(
                (b for b in blocks if _strip_title_marks(b["title"]) == title),
                None,
            )
            if target is None:
                return False
            clean = _strip_title_marks(target["title"])
            target["title"] = f"~~{clean}~~"
            marker = f"> [stale] {reason}".rstrip()
            if not any("stale" in ln for ln in target["body_lines"]):
                target["body_lines"] = [marker, *target["body_lines"]]
            target["source"] = source_session or target.get("source")
            target["created"] = _now()
            self._write_blocks(preamble, blocks)
            return True

    def remove_sections(self, source_session: str) -> int:
        """移除来源会话为该 run_id 的所有分块，返回移除数。"""
        if not source_session:
            return 0
        with self._lock:
            preamble, blocks = self._read_blocks()
            kept = [b for b in blocks if b.get("source") != source_session]
            removed = len(blocks) - len(kept)
            if removed == 0:
                return 0
            self._write_blocks(preamble, kept)
            return removed

    def remove_by_title(self, title_or_substring: str) -> int:
        """按标题子串删除块（忽略标记，大小写不敏感），返回删除数。"""
        if not title_or_substring:
            return 0
        needle = title_or_substring.lower()
        with self._lock:
            preamble, blocks = self._read_blocks()
            kept = [
                b for b in blocks
                if needle not in _strip_title_marks(b["title"]).lower()
            ]
            removed = len(blocks) - len(kept)
            if removed == 0:
                return 0
            self._write_blocks(preamble, kept)
            return removed

    def remove_by_keyword(self, keyword: str) -> int:
        """按内容/标题关键字删除块（大小写不敏感），返回删除数。"""
        if not keyword:
            return 0
        needle = keyword.lower()
        with self._lock:
            preamble, blocks = self._read_blocks()
            kept = [
                b for b in blocks
                if needle not in "\n".join(b["body_lines"]).lower()
                and needle not in b["title"].lower()
            ]
            removed = len(blocks) - len(kept)
            if removed == 0:
                return 0
            self._write_blocks(preamble, kept)
            return removed

    # ── 块解析内核（ADR-0021 机制4：共用） ──────────────────────────────────

    def _read_blocks(self) -> tuple[list[str], list[dict]]:
        """解析文件为 (preamble 行, 块列表)。块结构见 _parse_block。"""
        text = self.read()
        if not text:
            return [], []
        lines = text.splitlines()
        preamble: list[str] = []
        blocks: list[dict] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("## "):
                block = {
                    "title": line[3:],
                    "source": None,
                    "created": None,
                    "hash": None,
                    "body_lines": [],
                }
                i += 1
                while i < len(lines) and not lines[i].startswith("## "):
                    stripped = lines[i].strip()
                    if stripped.startswith("<!--") and "source:" in stripped:
                        block["source"] = _extract_meta(stripped, "source")
                        block["created"] = _extract_meta(stripped, "created")
                        block["hash"] = _extract_meta(stripped, "hash")
                    else:
                        block["body_lines"].append(lines[i])
                    i += 1
                blocks.append(block)
            else:
                preamble.append(line)
                i += 1
        return preamble, blocks

    def _write_blocks(self, preamble: list[str], blocks: list[dict]) -> None:
        """把 preamble + 块列表重写回文件（幂等写，锁外调用需自行持锁）。"""
        text = "\n".join(preamble).rstrip("\n")
        parts: list[str] = []
        for b in blocks:
            body = "\n".join(b["body_lines"]).strip()
            parts.append(
                f"## {b['title']}\n"
                f"<!-- source: {b.get('source') or ''}; created: {b.get('created') or _now()}; "
                f"hash: {b.get('hash') or _content_hash(body)} -->\n"
                f"{body}"
            )
        if parts:
            text = (text + "\n\n" if text else "") + "\n\n".join(parts)
        text = text.rstrip("\n") + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(text, encoding="utf-8")


def _extract_meta(comment_line: str, key: str) -> str | None:
    m = re.search(rf"{key}:\s*([^;]+);", comment_line)
    return m.group(1).strip() if m else None

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone


class MemoryStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                source_session TEXT,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                use_count INTEGER DEFAULT 0,
                confidence REAL DEFAULT 1.0,
                content_hash TEXT UNIQUE NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind)
        """)
        self.conn.commit()

    def ingest(
        self,
        content: str,
        kind: str = "episodic",
        source_session: str | None = None,
        confidence: float = 1.0,
    ) -> bool:
        content = content.strip()
        if not content:
            return False

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        existing = self.conn.execute(
            "SELECT id, use_count FROM memories WHERE content_hash=?", (content_hash,)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE memories SET last_used_at=?, use_count=? WHERE id=?",
                (_now(), existing[1] + 1, existing[0]),
            )
            self.conn.commit()
            return False

        now = _now()
        self.conn.execute(
            "INSERT INTO memories (kind, content, source_session, created_at, last_used_at, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, content, source_session, now, now, content_hash),
        )
        self.conn.commit()
        return True

    def search(self, query: str, k: int = 5) -> list[dict]:
        if not query.strip():
            return []
        terms = query.split()
        if not terms:
            return []
        like_clauses = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in terms)
        params = [f"%{t.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%" for t in terms]
        try:
            rows = self.conn.execute(
                f"SELECT content, kind, confidence, created_at "
                f"FROM memories WHERE {like_clauses} "
                f"ORDER BY (use_count * 100.0 / MAX(1, ROUND((julianday('now') - julianday(last_used_at)) * 1440 + 1))) DESC, last_used_at DESC LIMIT ?",
                (*params, k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {"content": r[0], "kind": r[1], "confidence": r[2], "created_at": r[3]}
            for r in rows
        ]

    def recent(self, kind: str = "episodic", limit: int = 5) -> list[dict]:
        """Return the most recently stored memories of a given kind."""
        try:
            rows = self.conn.execute(
                "SELECT content, kind, confidence, created_at "
                "FROM memories WHERE kind=? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {"content": r[0], "kind": r[1], "confidence": r[2], "created_at": r[3]}
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()




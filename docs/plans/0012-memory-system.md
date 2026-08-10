# 0012: 记忆系统

## 原则

- 零外部服务依赖，SQLite FTS5
- 增量蒸馏写入（与 auto-compact 协同）
- 两种注入策略（pinned 全量 + episodic BM25 检索 top-k）
- 幂等写入（content_hash 去重）

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `vague_code/agent/memory.py` | **新建** |
| 2 | `vague_code/agent/memory_tool.py` | **新建** |
| 3 | `vague_code/agent/config.py` | 改：加 `MemoryConfig` |
| 4 | `vague_code/agent/loop.py` | 改：pinned 注入 + 蒸馏集成 |
| 5 | `tests/test_memory.py` | **新建** |

## 步骤 1：`memory.py`

### MemoryStore

```python
class MemoryStore:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""CREATE TABLE IF NOT EXISTS memories (...)""")
        self.conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(...)""")

    def ingest(self, content: str, kind: str = "episodic", source_session: str | None = None) -> bool:
        hash = hashlib.sha256(content.encode()).hexdigest()
        if self.conn.execute("SELECT 1 FROM memories WHERE content_hash=?", (hash,)).fetchone():
            return False  # duplicate
        self.conn.execute("INSERT ...")
        return True

    def search(self, query: str, k: int = 5) -> list[dict]:
        # FTS5 BM25 search
        rows = self.conn.execute("""SELECT ... FROM memories_fts WHERE ... MATCH ? ORDER BY rank LIMIT ?""", (query, k))
        return [{"content": r[1], "kind": r[2]} for r in rows]

    def get_pinned(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT content FROM memories WHERE kind='pinned'")]

    def close(self):
        self.conn.close()
```

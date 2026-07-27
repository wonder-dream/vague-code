---
status: accepted
date: 2026-07-27
---

# 0014: 记忆系统

## 背景

跨会话记忆。统一记忆库 + pinned（常驻）/ episodic（情景按需检索）两种注入策略。与 auto-compact 压缩协同做增量蒸馏。

## 约束

1. **零外部服务**——SQLite 单文件存储（FTS5 做 BM25 检索），v2 加 sqlite-vec/FAISS
2. **与 auto-compact 协同**——压缩摘要直接复用为蒸馏输入
3. **幂等写入**——content_hash 去重，崩溃重跑不产生重复条目
4. **增量蒸馏**——每次 auto-compact 触发时新增记忆条目，不重写全部

## 架构

### 存储模型

```sql
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,           -- 'pinned' | 'episodic'
    content TEXT NOT NULL,
    source_session TEXT,          -- 来源会话 ID
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    use_count INTEGER DEFAULT 0,
    confidence REAL DEFAULT 1.0,
    content_hash TEXT UNIQUE NOT NULL  -- 幂等去重
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, content=memories, content_rowid=id
);
```

### 注入策略

- **pinned**：数量少（<10），全量注入 system prompt 静态段。存储时 `kind='pinned'`
- **episodic**：以当前 task 为 query，BM25 检索 top-k，包装为 `<memory>` block 注入

### 写入流程

1. auto_compact 触发 → 摘要文本 → `MemoryStore.ingest(summary, kind='episodic')`
2. 干净的 run 结束 → 时序（`_run_gen` 结束后）→ `MemoryStore.ingest(final_summary)`
3. ingest 内部：`hashlib.sha256(content).hexdigest()` → 检查 duplicates → 不重复则写入

### 检索

```python
def search(self, query: str, k: int = 5) -> list[dict]:
    # BM25 via FTS5
    rows = self.conn.execute(
        "SELECT rank, content FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, k),
    ).fetchall()
```

## Consequences

- v1 纯 BM25，v2 加 dense 后只需替换 `search` 方法
- 记忆库路径与 trajectory 同目录：`runs/memory.db`
- 注入的 pinned 记忆吃 KV Cache，量小可忽略

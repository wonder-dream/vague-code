---
status: accepted
date: 2026-07-27
---

# 0014: 记忆系统

## 背景

跨会话记忆。统一记忆库 + episodic（情景按需检索）注入策略。与 auto-compact 压缩协同做增量蒸馏。

> **决策更新（2026-08-01）：** pinned（常驻）注入被判定为伪需求——生效范围是全局 memory.db、无项目隔离，与用途（项目约定）不匹配；且 `.agent/rules.md` 层级加载（ADR-0008）可完整替代。**pinned 已移除，`kind` 列保留但只余 `'episodic'`。**

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
    kind TEXT NOT NULL,           -- 'episodic'
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

- **episodic**：Agent 通过 `memory_search` 工具按需检索（LIKE 子句 + 热度排序）

### 写入流程

1. auto_compact 触发 → 摘要文本 → `MemoryStore.ingest(summary, kind='episodic')`
2. ingest 内部：`hashlib.sha256(content).hexdigest()` → 检查 duplicates → 不重复则写入

### 检索

```python
def search(self, query: str, k: int = 5) -> list[dict]:
    # LIKE 子句 + 热度排序（use_count × 100 / minutes_since_last_use）
    terms = query.split()
    like_clauses = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in terms)
    ...
```

## Consequences

- v1 LIKE 检索，v2 可替换为 FTS5/BM25 或 dense（只需改 `search` 方法）
- 记忆库路径与 trajectory 同目录：`runs/memory.db`
- 常驻知识由 `.agent/rules.md` 层级加载承担（ADR-0008）

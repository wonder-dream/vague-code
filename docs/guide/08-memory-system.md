# 细纲：08-memory-system.md

**预估行数：** ~350 行
**定位：** 跨会话记忆系统的完整设计。

---

## 开头

- **谁需要读：** 想理解跨会话记忆机制的开发者
- **前置阅读：** 07-permission-system.md
- **读完能做什么：** 了解 episodic 记忆的检索与写入流程

---

## 细纲

### 1. 概述（~30 行）

- 设计边界：**记忆 ≠ 上下文**。记忆存长期知识（跨会话），上下文管当前对话
- 统一记忆库（SQLite）+ episodic 按需检索：
  - **Episodic（按需检索）：** Agent 通过 `memory_search` 工具主动搜索
  - ~~Pinned（常驻注入）：~~ 已移除（ADR-0016 配套决策），常驻知识由 `.agent/rules.md`（ADR-0008）承担
- 写入走 auto_compact 蒸馏，检索走 LIKE + 热度排序
- ADR-0014 的设计动机

### 2. 存储模型（~50 行）

**`MemoryStore` 类（`memory.py:8-101`）：**

**表结构（`memory.py:16-28 ` SCHEMA）：**

| 字段 | 类型 | 约束 | 用途 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键 |
| kind | TEXT | NOT NULL | `"episodic"` |
| content | TEXT | NOT NULL | 记忆内容 |
| source_session | TEXT | — | 来源会话 ID |
| created_at | TEXT | NOT NULL | ISO 8601 创建时间 |
| last_used_at | TEXT | NOT NULL | 最后命中时间 |
| use_count | INTEGER | DEFAULT 0 | 命中次数的计数 |
| confidence | REAL | DEFAULT 1.0 | 置信度（预留） |
| content_hash | TEXT | UNIQUE NOT NULL | SHA-256 去重 |

```sql
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
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
```

**索引：** `idx_memories_kind`（按 kind 检索）

**连接模式：** `PRAGMA journal_mode=WAL` + `check_same_thread=False`（线程安全）

### 3. Episodic 检索（~50 行）

**`memory_search` 工具（`memory_tool.py:5-32`）：**

**动态注册（`loop.py:232-236`）：**
```python
if self._memory_store and task.strip():
    from src.agent.memory_tool import MEMORY_SEARCH_SPEC, make_memory_search_handler
    self._tool_specs.append(MEMORY_SEARCH_SPEC)
    memory_search_handler = make_memory_search_handler(self._memory_store)
    bound_tools["memory_search"] = memory_search_handler
```

**搜索算法（`memory.py:66-86`）：**
```python
def search(self, query: str, k: int = 5) -> list[dict]:
    # 分词组 LIKE 查询
    terms = query.split()
    like_clauses = " OR ".join("content LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms]

    # 热度排序
    rows = self.conn.execute(
        f"SELECT ... FROM memories WHERE {like_clauses} "
        f"ORDER BY (use_count * 100.0 / "
        f"  MAX(1, ROUND((julianday('now') - julianday(last_used_at)) * 1440 + 1))"
        f") DESC, last_used_at DESC LIMIT ?",
        (*params, k),
    )
```

**转义处理：** `"\\"` → `"\\\\"`、`"%"` → `"\\%"`、`"_"` → `"\\_"`（`memory.py:73`）

**热度因子详解：**
- 分子：`use_count × 100`（使用越多越热）
- 分母：`MAX(1, minutes_since_last_use + 1)`（越久未用越冷）
- 结果：`julianday('now') - julianday(last_used_at) × 1440`（分钟差）

**返回格式：**
```
--- Memory (confidence: 1.0) ---
用户偏好使用 pytest 而非 unittest

--- Memory (confidence: 0.8) ---
项目约定：数据库连接用 asyncpg
```

### 4. 写入管道（~50 行）

**`ingest()`（`memory.py:34-64`）：**
```python
def ingest(self, content: str, kind: str = "episodic",
           source_session: str | None = None, confidence: float = 1.0) -> bool:
    content = content.strip()
    if not content:
        return False

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # 去重：已有相同 hash → update 使用计数和时间
    existing = self.conn.execute(
        "SELECT id, use_count FROM memories WHERE content_hash=?", (content_hash,)
    ).fetchone()
    if existing:
        self.conn.execute(
            "UPDATE memories SET last_used_at=?, use_count=? WHERE id=?",
            (_now(), existing[1] + 1, existing[0]),
        )
        return False  # 不是新记忆

    # 新纪录
    now = _now()
    self.conn.execute(
        "INSERT INTO memories (...) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, content, source_session, now, now, content_hash),
    )
    return True
```

**会话蒸馏路径（`loop.py:323-329`）：**
```python
for r in reports:
    if r.layer == "auto_compact" and r.affected > 0 and r.detail.get("summary_text"):
        self._memory_store.ingest(
            content=r.detail["summary_text"],
            kind="episodic",
            source_session=traj.run_id,
        )
```

**SHA-256 去重：** 内容完全相同时不重复写入，仅更新 `last_used_at` 和 `use_count`

### 5. 评分与排序（~30 行）

**热度公式拆解：**
```
score = (use_count × 100) / MAX(1, minutes_since_last_use + 1)
```

| 场景 | use_count | 距最后使用（分钟） | 得分 | 隐含含义 |
|------|-----------|-------------------|------|---------|
| 高频且刚用 | 50 | 5 | 833 | 最相关 |
| 高频但久不用 | 50 | 10080（7 天） | 0.5 | 历史高频但过时 |
| 低频但刚用 | 1 | 5 | 16.6 | 刚产生的新记忆 |
| 低频且久不用 | 1 | 10080 | 0.01 | 几乎被遗忘 |

### 6. 与压缩的协同——蒸馏（~30 行）

- auto_compact 产出的摘要 → `ingest()` → episodic 记忆库
- 设计意图：长会话的自然总结即长期知识
- 下次会话中，Agent 可通过 `memory_search` 召回这些摘要
- 配置：`MemoryConfig.auto_compact_distill`（默认 True）

### 7. 配置参考（~20 行）

**`MemoryConfig`（`config.py:53-59`）：**

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | True | 是否启用记忆系统（false→不读写记忆） |
| memory_db_path | str | `"runs/memory.db"` | SQLite 数据库文件路径 |
| search_top_k | int | 5 | `memory_search` 返回的最大结果数 |
| auto_compact_distill | bool | True | auto_compact 摘要是否自动蒸馏为 episodic 记忆 |
| auto_compact_distill | bool | True | auto_compact 摘要是否自动蒸馏为 episodic 记忆 |

---

## 结尾

**下一篇推荐：** → 09-model-abstraction.md（统一的 LLM 接口抽象）
**相关 ADR：** 0014（Memory System）
**相关 plans：** 0012（memory-system）

---

## 本文件说明

这是文档 `08-memory-system.md` 的细纲（大纲）。实际写作时需确认 FTS5 索引是否已启用（当前使用 LIKE 查询），需要更新说明。

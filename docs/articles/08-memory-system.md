# Memory System

**谁需要读：** 想理解跨会话记忆机制的开发者
**前置阅读：** 07-permission-system.md
**读完能做什么：** 了解 episodic 记忆的检索与写入流程

---

## 1. 概述

**记忆 ≠ 上下文。** 这是 Memory System 的第一设计边界。上下文（Context Engineering）管理当前对话的 token 窗口，是短期的、瞬态的。记忆负责长期知识——跨会话、可检索、持续积累。

vague-code 的记忆系统围绕一个统一记忆库（SQLite）构建，提供按需检索的 episodic 注入策略：

- **Episodic（情景记忆）：** 按需检索。Agent 通过 `memory_search` 工具主动搜索历史经验。

> **设计更新：** pinned（常驻注入）已被移除（ADR-0016 配套决策）。原 pinned 承担的"常驻知识"职责由 `.agent/rules.md` 层级加载（ADR-0008）替代——因为 pinned 生效范围是全局 memory.db、无项目隔离，与"项目约定"的用途不匹配。

写入走 auto_compact 蒸馏——长会话的自动摘要自然成为新的长期记忆。检索使用 LIKE 子句 + 热度排序，不需要第三方向量数据库。

ADR-0014 的设计动机：记忆系统必须和压缩系统协同工作，而不是独立运行。压缩产生摘要，摘要蒸馏为记忆，记忆在下一次会话中被召回。

---

## 2. 存储模型

**MemoryStore** 类（`memory.py:8-101`）管理 SQLite 统一记忆库。

**表结构：**

| 字段 | 类型 | 约束 | 用途 |
|------|------|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 主键 |
| kind | TEXT | NOT NULL | `"episodic"` |
| content | TEXT | NOT NULL | 记忆内容 |
| source_session | TEXT | — | 来源会话 ID |
| created_at | TEXT | NOT NULL | ISO 8601 创建时间 |
| last_used_at | TEXT | NOT NULL | 最后命中时间 |
| use_count | INTEGER | DEFAULT 0 | 命中次数 |
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

**索引：** `idx_memories_kind` 按 kind 快速区分记忆类别（当前仅 episodic）。

**连接模式：** `PRAGMA journal_mode=WAL` + `check_same_thread=False`，保证跨线程访问安全。

---

## 3. Episodic 检索

### 动态注册

`memory_search` 工具不在 DEFAULT_TOOLS 中——它在 `loop.py` 动态注册：

```python
if self._memory_store and task.strip():
    from vague_code.agent.memory_tool import MEMORY_SEARCH_SPEC, make_memory_search_handler
    self._tool_specs.append(MEMORY_SEARCH_SPEC)
    memory_search_handler = make_memory_search_handler(self._memory_store)
    bound_tools["memory_search"] = memory_search_handler
```

条件：`memory.enabled=True` 且 task 非空。无任务时 Agent 不需要搜索记忆。

### 搜索算法

**代码位置：** `memory.py:66-86`

```python
def search(self, query: str, k: int = 5) -> list[dict]:
    terms = query.split()
    like_clauses = " OR ".join("content LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms]

    rows = self.conn.execute(
        f"SELECT ... FROM memories WHERE {like_clauses} "
        f"ORDER BY (use_count * 100.0 / "
        f"  MAX(1, ROUND((julianday('now') - julianday(last_used_at)) * 1440 + 1))"
        f") DESC, last_used_at DESC LIMIT ?",
        (*params, k),
    )
```

使用 `LIKE` 查询——分词后每个词独立匹配，返回按热度排序的 top-K 结果。

**转义处理**（`memory.py:73`）：`\` → `\\`、`%` → `\%`、`_` → `\_`，防止 LIKE 通配符注入。

**热度因子：**
- 分子：`use_count × 100`（使用越多越热）
- 分母：`MAX(1, minutes_since_last_use + 1)`（越久未用越冷）
- 分时差：`julianday('now') - julianday(last_used_at) × 1440`（分钟差）

**返回格式：**

```
--- Memory (confidence: 1.0) ---
用户偏好使用 pytest 而非 unittest

--- Memory (confidence: 0.8) ---
项目约定：数据库连接用 asyncpg
```

---

## 4. 写入管道

### ingest() 写入

**代码位置：** `memory.py:34-64`

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
        return False

    # 新记录
    now = _now()
    self.conn.execute(
        "INSERT INTO memories (...) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, content, source_session, now, now, content_hash),
    )
    return True
```

**SHA-256 去重：** 内容完全相同时不重复写入，仅更新 `last_used_at` 和 `use_count`。这意味着同一段知识被多次提及时，其热度会自然上升。

### 会话蒸馏

**代码位置：** `loop.py:323-329`

```
for r in reports:
    if r.layer == "auto_compact" and r.affected > 0 and r.detail.get("summary_text"):
        self._memory_store.ingest(
            content=r.detail["summary_text"],
            kind="episodic",
            source_session=traj.run_id,
        )
```

auto_compact 产出的摘要自动蒸馏为 episodic 记忆。设计意图：长会话的自然总结即长期知识。下次会话中，Agent 可通过 `memory_search` 召回这些摘要。

---

## 5. 评分与排序

热度公式：`score = (use_count × 100) / MAX(1, minutes_since_last_use + 1)`

几个典型场景下的得分直观理解：

| 场景 | use_count | 距最后使用（分钟） | 得分 | 隐含含义 |
|------|-----------|-------------------|------|---------|
| 高频且刚用 | 50 | 5 | 833 | 最相关 |
| 高频但久不用 | 50 | 10080（7 天） | 0.5 | 历史高频但可能过时 |
| 低频但刚用 | 1 | 5 | 16.6 | 刚产生的新记忆 |
| 低频且久不用 | 1 | 10080 | 0.01 | 几乎被遗忘 |

高频且刚用的记忆得分最高，低频且久不用的最低。这个公式不需要额外的参数调优，在 SQL 中直接计算。

---

## 6. 与压缩的协同——蒸馏

Memory System 与 Context Engineering 的协同通过蒸馏路径实现：

```
auto_compact → 摘要 → ingest() → episodic 记忆库 → 下次会话 memory_search 可召回
```

循环流程：
1. 上下文利用率 > 85% 时触发 auto_compact
2. auto_compact 生成历史摘要
3. 摘要通过 `ingest()` 写入 episodic 记忆库（SHA-256 去重）
4. 下次会话中，Agent 可通过 `memory_search` 搜索到这些摘要

配置开关：`MemoryConfig.auto_compact_distill`（默认 True）。蒸馏不需要额外的 LLM 调用——复用了 auto_compact 的摘要结果。

---

## 7. 配置参考

**MemoryConfig**（`config.py:53-59`）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | True | 是否启用记忆系统 |
| memory_db_path | str | `"runs/memory.db"` | SQLite 数据库文件路径 |
| search_top_k | int | 5 | memory_search 返回的最大结果数 |
| auto_compact_distill | bool | True | auto_compact 摘要是否自动蒸馏为 episodic 记忆 |

---

## 下一篇

→ **09-model-abstraction.md**：统一的 LLM 接口抽象——IR 类型、Codec 架构、流事件系统。

**相关 ADR：** 0014（Memory System）
**相关 plans：** 0012（memory-system）

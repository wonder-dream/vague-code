# 细纲：10-trajectory.md

**预估行数：** ~300 行
**定位：** 轨迹事件溯源系统的完整设计。

---

## 开头

- **谁需要读：** 想理解 Agent 运行记录存储机制的开发者
- **前置阅读：** 04-agent-runtime.md（了解轨迹的使用场景）
- **读完能做什么：** 读取轨迹数据、实现崩溃恢复、编写轨迹查询

---

## 细纲

### 1. 概述（~30 行）

- 为什么事件溯源（event sourcing）而非状态存储：
  - 消息数组丢失元数据（token usage、时间戳、延迟、压缩率）
  - 事件流完整可审计，支持离线重算和失败重分类
- 双存储策略：JSONL 不可变 + SQLite 可查询
- 轨迹是 Agent 和评测工具的桥梁数据结构（ADR-0003）
- `Trajectory` dataclass（`trajectory.py:121-124`）：`run_id + config + events`

### 2. EventType 枚举（~40 行）

**代码位置：** `trajectory.py:24-37`

| 事件类型 | 枚举值 | 触发时机 | 代码位置 |
|---------|--------|---------|---------|
| run_start | `"run_start"` | Agent.start() 初始化完成 | `loop.py:210` |
| turn_start | `"turn_start"` | 每轮开始 | `loop.py:252` |
| compression | `"compression"` | 压缩后（含 budget 监控） | `loop.py:288-307` |
| stream_event | `"stream_event"` | 每个流式 chunk 到达 | `loop.py:374-375` |
| retry | `"retry"` | 每次重试时 | `loop.py:362-368` |
| llm_response | `"llm_response"` | LLM 调用完成 | `loop.py:378-381` |
| permission_check | `"permission_check"` | 每次权限判定 | `loop.py:519-522` |
| tool_call | `"tool_call"` | 工具调用开始时 | `loop.py:450,464` |
| tool_result | `"tool_result"` | 工具执行完毕 | `loop.py:459,474` |
| error | `"error"` | 异常/超时/任何非正常 | `loop.py:273,349,445` 等 |
| run_end | `"run_end"` | 运行结束 | `loop.py:389-405` |
| mode_change | `"mode_change"` | 权限模式切换 | TUI `/mode` |

**`Event` dataclass（`trajectory.py:40-68`）：**

| 属性 | 类型 | 说明 |
|------|------|------|
| run_id | str | 所属 run |
| turn | int\|None | 所属轮次（run_start/run_end=None） |
| ts | float | Unix 时间戳 |
| type | EventType | 事件类型 |
| payload | dict | 事件荷载 |

- `to_dict()` → JSONL 序列化
- `to_row()` → SQLite 写入（5 元组）

### 3. SQLite 存储（~50 行）

**双表结构（`trajectory.py:71-88`）：**

**`runs` 表（概要信息）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | TEXT PRIMARY KEY | 唯一标识 |
| task | TEXT | 任务描述 |
| workdir | TEXT | 工作目录 |
| config_json | TEXT | `AgentConfig.to_public_dict()` 的 JSON |
| status | TEXT | 结束原因（end_turn / max_turns / error 等） |
| created_at | REAL | 创建时间戳 |

**`events` 表（详细事件流）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | TEXT | 所属 run（复合索引） |
| turn | INTEGER | 轮次（可为 NULL） |
| ts | REAL | 时间戳 |
| type | TEXT | 事件类型字符串 |
| payload | TEXT | JSON 文本 |

- 索引：`idx_events_run_id ON events(run_id)`（`trajectory.py:88`）

**`persist()`（`trajectory.py:257-281`）——增量写入：**
- WAL 模式 + 5 秒 busy_timeout（`trajectory.py:263-264`）
- `new_events = events[persisted_count:]`（`trajectory.py:269`）
- `runs` → `INSERT OR REPLACE`（幂等）
- `events` → `executemany`（增量）

**`from_db()`（`trajectory.py:130-185`）——重建：**
- 从 `runs` 表读 config_json → 过滤未知 AgentConfig 字段（防序列化失败）
- 从 `events` 表按 rowid 顺序读 → 重建 `list[Event]`
- 设置 `_persisted_count = len(events)`（防重复写入）

**`Run.from_events()`（`trajectory.py:100-114`）：**
- 从事件流重建 Run 记录（首次 persist 时用）

### 4. to_messages()（~50 行）

**代码位置：** `trajectory.py:198-250`

**事件流 → LLM 消息数组的转换逻辑：**

```python
for ev in events:
    if ev.type == "run_start":
        → system_prompt → Message(role="system")
        → task → Message(role="user")
    elif ev.type == "llm_response":
        → flush pending ToolResults
        → _decode_block() 逐个 Block → Message(role="assistant")
    elif ev.type == "tool_result":
        → 缓冲到 pending_tool_results
        → 去重：`seen_tool_result_ids`
flush 剩余的 pending_tool_results
```

**去重逻辑（`trajectory.py:234-236`）：**
```python
if tid and tid in seen_tool_result_ids:
    continue
seen_tool_result_ids.add(tid)
```
防止 resume 时重复的 tool_result 事件导致重复消息

**`_decode_block()`（`trajectory.py:284-304`）：**
- dict → Block 的反序列化
- `"type"` 字段分发到 TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock
- 未知 type → return None

**用途链：** `from_db()` → `to_messages()` → `Agent.resume()`（`loop.py:582`）

### 5. export_jsonl()（~20 行）

**代码位置：** `trajectory.py:252-255`

```python
def export_jsonl(self, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in self.events:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=True) + "\n")
```

### 6. Resume 全流程（~60 行）

**代码位置：** `loop.py:569-606` `Agent.resume()`

```
1. 检查轨迹是否已完成
   any(event.type == EventType.run_end) → 直接返回（幂等）

2. _validate_consistent(traj)
   对比 run_start 中的 config 与当前 model/tools
   model 不同→警告，tools 减少→报错

3. from_db(run_id, db_path) → 从 SQLite 重建事件流

4. to_messages() → 重建 LLM 可消费的 messages 数组

5. 分析最后一条 llm_response
   last = reversed(events) 中第一个 type == "llm_response"

   if terminal (end_turn, stop_sequence, max_tokens, ...):
       emit(run_end)
       return traj  # 已完成，无需恢复

   if max_turns 已满:
       emit(run_end, "max_turns")
       return traj

   # 有未执行的工具 → 回放
   _execute_pending_tools(traj, messages, turn, bound_tools)
   next_turn = T + 1

6. 恢复 _run_gen(traj, messages, [next_turn], bound_tools)
   for _ in _run_gen:
       pass  # exhaust
   return traj
```

**`_validate_consistent()`（`loop.py:608-625`）：**
- 从 run_start event 提取存储的 config
- 检查 model：不同则警告（`loop.py:613-614`）
- 检查 tools：当前工具集必须包含存储时的所有工具（子集校验，`loop.py:616-621`）

**`_execute_pending_tools()`（`loop.py:627-680`）：**
- 确认 `messages[-1].role == "assistant"`
- 遍历 assistant message 的 ToolUseBlock
- 找到尚未有 tool_result event 的 pending tools
- 逐个执行（权限检查 `check_confirm=False`，不弹窗）

### 7. 查询模式（~30 行）

**常用 SQL 查询（可直接复制使用的示例代码块）：**

| 查询目的 | SQL |
|---------|-----|
| 列出所有 run | `SELECT run_id, task, status, created_at FROM runs ORDER BY created_at DESC` |
| 事件类型分布 | `SELECT type, COUNT(*) FROM events WHERE run_id='X' GROUP BY type` |
| token 消耗 | `SELECT SUM(json_extract(payload, '$.usage.input_tokens')), SUM(json_extract(payload, '$.usage.output_tokens')) FROM events WHERE run_id='X' AND type='llm_response'` |
| 各层压缩回收 | `SELECT json_extract(payload, '$.layer'), SUM(json_extract(payload, '$.before_tokens') - json_extract(payload, '$.after_tokens')) FROM events WHERE run_id='X' AND type='compression' AND layer!='budget' GROUP BY layer` |
| 错误事件 | `SELECT turn, json_extract(payload, '$.kind'), json_extract(payload, '$.message') FROM events WHERE run_id='X' AND type='error'` |
| 权限判定 | `SELECT json_extract(payload, '$.tool'), json_extract(payload, '$.decision') FROM events WHERE run_id='X' AND type='permission_check'` |
| JSONL 导出 | `SELECT json_object('run_id',run_id,'turn',turn,'ts',ts,'type',type,'payload',json(payload)) FROM events WHERE run_id='X' ORDER BY rowid` |

---

## 结尾

**下一篇推荐：** → 11-cli-and-tui.md（两个用户界面实现）
**相关 ADR：** 0003（Event-Sourced Trajectory）
**相关 plans：** 0002-section（agent-loop 轨迹部分）

---

## 本文件说明

这是文档 `10-trajectory.md` 的细纲（大纲）。写作时需确认 SQLite 表结构与实际 schema 一致。`to_messages()` 部分需与 `resume()` 的测试路径同步验证。

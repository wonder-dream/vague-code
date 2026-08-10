# Trajectory

**谁需要读：** 想理解 Agent 运行记录存储机制的开发者
**前置阅读：** 04-agent-runtime.md（了解轨迹的使用场景）
**读完能做什么：** 读取轨迹数据、实现崩溃恢复、编写轨迹查询

---

## 1. 概述

为什么不用简单的消息数组？因为消息数组天然丢失元数据——token usage、时间戳、延迟、压缩率都只存在于运行时。当需要分析一次运行的成本、定位失败原因、或做消融对比时，消息数组远远不够。

vague-code 使用**事件溯源（event sourcing）**：每次运行记录为一个不可变的事件流，每行一个类型化事件，携带完整的上下文元数据。

双存储策略：
- **SQLite**：结构化存储，支持查询和重建
- **JSONL**：按需导出，供外部分析工具消费

轨迹是 Agent 与评测工具的桥梁数据结构（ADR-0003）。评测 harness 通过 `Trajectory.to_messages()` 将事件流转换为 LLM-as-Judge 可消费的消息数组。

**Trajectory dataclass**（`trajectory.py:121-124`）：`run_id + config + events` 的三元组。

---

## 2. EventType 枚举

**代码位置：** `trajectory.py:24-37`

12 种事件类型覆盖一次运行的所有关键节点：

| 事件类型 | 触发时机 | 代码位置 |
|---------|---------|---------|
| run_start | Agent.start() 初始化完成 | `loop.py:210` |
| turn_start | 每轮开始 | `loop.py:252` |
| compression | 压缩后（含 budget 监控） | `loop.py:288-307` |
| stream_event | 每个流式 chunk 到达 | `loop.py:374-375` |
| retry | 每次重试 | `loop.py:362-368` |
| llm_response | LLM 调用完成 | `loop.py:378-381` |
| permission_check | 每次权限判定 | `loop.py:519-522` |
| tool_call | 工具调用开始 | `loop.py:450,464` |
| tool_result | 工具执行完毕 | `loop.py:459,474` |
| error | 异常/超时/非正常 | `loop.py:273,349,445` |
| run_end | 运行结束 | `loop.py:389-405` |
| mode_change | 权限模式切换 | TUI `/mode` |

**Event dataclass**（`trajectory.py:40-68`）：

| 属性 | 类型 | 说明 |
|------|------|------|
| run_id | str | 所属 run |
| turn | int\|None | 所属轮次 |
| ts | float | Unix 时间戳 |
| type | EventType | 事件类型 |
| payload | dict | 事件荷载 |

每个 Event 提供 `to_dict()`（JSONL 序列化）和 `to_row()`（SQLite 写入）两个方法。

---

## 3. SQLite 存储

**双表结构**（`trajectory.py:71-88`）：

**runs 表**——一次运行一条，记录概要信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | TEXT PRIMARY KEY | 唯一标识 |
| task | TEXT | 任务描述 |
| workdir | TEXT | 工作目录 |
| config_json | TEXT | `AgentConfig.to_public_dict()` 的 JSON |
| status | TEXT | 结束原因（end_turn / max_turns / error 等） |
| created_at | REAL | 创建时间戳 |

**events 表**——事件流明细：

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | TEXT | 所属 run（复合索引） |
| turn | INTEGER | 轮次（可为 NULL） |
| ts | REAL | 时间戳 |
| type | TEXT | 事件类型字符串 |
| payload | TEXT | JSON 文本 |

索引：`idx_events_run_id ON events(run_id)`（`trajectory.py:88`）

### persist()——增量写入

**代码位置：** `trajectory.py:257-281`

- WAL 模式 + 5 秒 busy_timeout（`trajectory.py:263-264`）
- `new_events = events[persisted_count:]`——只写新事件
- `runs` → `INSERT OR REPLACE`（幂等）
- `events` → `executemany`（批量写入）

### from_db()——重建

**代码位置：** `trajectory.py:130-185`

1. 从 `runs` 表读 config_json，过滤未知 AgentConfig 字段防止序列化失败
2. 从 `events` 表按 rowid 顺序读，重建 `list[Event]`
3. 设置 `_persisted_count = len(events)`，防止重复写入

---

## 4. to_messages()

**代码位置：** `trajectory.py:198-250`

事件流 → LLM 消息数组的转换。这是 `resume()` 的核心——把持久化的事件流还原为 LLM 可消费的消息序列。

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
        → 去重：seen_tool_result_ids
flush 剩余的 pending_tool_results
```

**去重逻辑**（`trajectory.py:234-236`）：

```python
if tid and tid in seen_tool_result_ids:
    continue
seen_tool_result_ids.add(tid)
```

防止 resume 时重复的 tool_result 事件导致消息数组中重复的 ToolResultBlock。

**_decode_block()**（`trajectory.py:284-304`）：dict → Block 的反序列化，按 `"type"` 字段分发到四种 Block 类型。未知 type 返回 None 静默跳过。

**用途链：** `from_db()` → `to_messages()` → `Agent.resume()`（`loop.py:582`）

---

## 5. export_jsonl()

**代码位置：** `trajectory.py:252-255`

```python
def export_jsonl(self, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in self.events:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=True) + "\n")
```

每行一个 JSON 对象，携带事件类型、时间戳、payload 等消息数组天然丢失的元数据。与 SQLite 存储互补：JSONL 可读、易于外部工具消费；SQLite 可查询、支持程序化重建。

---

## 6. Resume 全流程

**代码位置：** `loop.py:569-606` `Agent.resume()`

```
1. 检查轨迹是否已完成
   any(event.type == EventType.run_end) → 直接返回（幂等）

2. _validate_consistent(traj)
   对比 run_start 中的 config 与当前 model/tools
   model 不同 → 警告，tools 减少 → 报错

3. from_db(run_id, db_path) → 从 SQLite 重建事件流

4. to_messages() → 重建 LLM 可消费的 messages 数组

5. 分析最后一条 llm_response
   last = reversed(events) 中第一个 type == "llm_response"

   if terminal (end_turn, stop_sequence, max_tokens, ...):
       emit(run_end)
       return traj        # 已完成，无需恢复

   if max_turns 已满:
       emit(run_end, "max_turns")
       return traj

   # 有未执行的工具 → 回放
   _execute_pending_tools(traj, messages, turn, bound_tools)
   next_turn = T + 1

6. 恢复 _run_gen(traj, messages, [next_turn], bound_tools)
   for _ in _run_gen:
       pass               # exhaust
   return traj
```

**_validate_consistent()**（`loop.py:608-625`）：从 run_start event 提取存储的 config，检查 model（不同则警告）和 tools（当前工具集必须包含存储时的所有工具，子集校验）。

**_execute_pending_tools()**（`loop.py:627-680`）：确认 `messages[-1].role == "assistant"`，遍历 assistant message 的 ToolUseBlock，找到尚未有 tool_result event 的 pending tools，逐个执行（权限检查 `check_confirm=False`，不弹窗）。

---

## 7. 查询模式

常用 SQL 查询（可直接复制使用）：

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

## 下一篇

→ **11-cli-and-tui.md**：两个用户界面实现——CLI（Rich）和 TUI（Textual）。

**相关 ADR：** 0003（Event-Sourced Trajectory）
**相关 plans：** 0002-section（agent-loop 轨迹部分）

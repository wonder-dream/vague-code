# 细纲：trajectory-events.md

**预估行数：** ~200 行（表格风格）
**定位：** API 参考——轨迹事件。

---

## 开头

- **谁需要读：** 需要查询/分析轨迹数据的开发者
- **前置阅读：** 10-trajectory.md
- **读完能做什么：** 理解每种事件的触发条件、payload 结构、SQL 查询模式

---

## 细纲

### 1. EventType 枚举

**代码位置：** `trajectory.py:24-37`

| 事件 | 枚举值 | 触发时机 | 代码位置 | 是否含 turn |
|------|--------|---------|---------|------------|
| run_start | `"run_start"` | Agent.start() 完成 | `loop.py:210` | × |
| turn_start | `"turn_start"` | 每轮开始 | `loop.py:252` | ✓ |
| compression | `"compression"` | 压缩后 / budget 监控 | `loop.py:288-307` | ✓ |
| stream_event | `"stream_event"` | 每个流式 chunk | `loop.py:374-375` | ✓ |
| retry | `"retry"` | 重试时 | `loop.py:362-368` | ✓ |
| llm_response | `"llm_response"` | LLM 调用完成 | `loop.py:378-381` | ✓ |
| permission_check | `"permission_check"` | 每次权限判定 | `loop.py:519-522` | ✓ |
| tool_call | `"tool_call"` | 工具调用开始 | `loop.py:450,464` | ✓ |
| tool_result | `"tool_result"` | 工具执行完毕 | `loop.py:459,474` | ✓ |
| error | `"error"` | 异常/超时 | `loop.py:273,349,445` | ✓ |
| run_end | `"run_end"` | 运行结束 | `loop.py:389-405` | × |
| mode_change | `"mode_change"` | 权限模式切换 | TUI `/mode` | × |

### 2. 每种事件的 payload 字段

**run_start：** task / workdir / system_prompt / config / tools

**turn_start：** 无 payload

**compression：** layer / before_tokens / after_tokens / affected / budget / skip_thinking / utilization / detail

**stream_event：** StreamEvent.to_dict() 全部字段

**retry：** attempt / delay_s / reason / exception / estimated_input_tokens

**llm_response：** stop_reason / usage (NormalizedUsage) / blocks (list[Block.to_dict()])

**permission_check：** tool / decision / command（截断至 200 字符）

**tool_call：** id / name / input

**tool_result：** tool_use_id / content / is_error

**error：** kind / message / attempts（仅retry_exhausted）/ last_error_kind

**run_end：** reason / pending_tool_calls

**mode_change：** from_mode / to_mode

### 3. JSONL 示例

```jsonl
{"run_id": "abc123", "turn": null, "ts": 1722000000.0, "type": "run_start", "payload": {"task": "fix bug", "workdir": "/tmp", ...}}
{"run_id": "abc123", "turn": 0, "ts": 1722000001.0, "type": "turn_start", "payload": {}}
{"run_id": "abc123", "turn": 0, "ts": 1722000002.0, "type": "compression", "payload": {"layer": "budget", "before_tokens": 5000, "after_tokens": 5000, "affected": 0, "budget": 900000, "utilization": 0.0056, "skip_thinking": true}}
{"run_id": "abc123", "turn": 0, "ts": 1722000003.0, "type": "llm_response", "payload": {"stop_reason": "tool_use", "usage": {"input_tokens": 5000, "output_tokens": 150, "cache_read_tokens": 0, "cache_write_tokens": 0}, "blocks": [{"type": "text", "text": "我来搜索..."}, {"type": "tool_use", "id": "call_1", "name": "grep", "input": {"pattern": "function"}}]}}
{"run_id": "abc123", "turn": 0, "ts": 1722000004.0, "type": "permission_check", "payload": {"tool": "grep", "decision": "allow", "command": ""}}
{"run_id": "abc123", "turn": 0, "ts": 1722000005.0, "type": "tool_call", "payload": {"id": "call_1", "name": "grep", "input": {"pattern": "function"}}}
{"run_id": "abc123", "turn": 0, "ts": 1722000006.0, "type": "tool_result", "payload": {"tool_use_id": "call_1", "content": "stats.py:42: def top_rated(...)", "is_error": false}}
{"run_id": "abc123", "turn": 1, "ts": 1722000007.0, "type": "turn_start", "payload": {}}
...
{"run_id": "abc123", "turn": null, "ts": 1722000010.0, "type": "run_end", "payload": {"reason": "end_turn"}}
```

### 4. 常用 SQL 查询

| 查询目的 | SQL |
|---------|-----|
| 列出所有 run | `SELECT run_id, task, status, created_at FROM runs ORDER BY created_at DESC` |
| 事件类型分布 | `SELECT type, COUNT(*) FROM events WHERE run_id='X' GROUP BY type` |
| token 统计 | `SELECT SUM(json_extract(payload, '$.usage.input_tokens')), SUM(json_extract(payload, '$.usage.output_tokens')) FROM events WHERE run_id='X' AND type='llm_response'` |
| 压缩回收 | `SELECT json_extract(payload, '$.layer'), SUM(json_extract(payload, '$.before_tokens') - json_extract(payload, '$.after_tokens')) FROM events WHERE run_id='X' AND type='compression' AND layer!='budget' GROUP BY layer` |
| 错误事件 | `SELECT turn, json_extract(payload, '$.kind'), json_extract(payload, '$.message') FROM events WHERE run_id='X' AND type='error'` |
| 权限判定 | `SELECT json_extract(payload, '$.tool'), json_extract(payload, '$.decision') FROM events WHERE run_id='X' AND type='permission_check'` |
| JSONL 导出 | `SELECT json_object('run_id',run_id,'turn',turn,'ts',ts,'type',type,'payload',json(payload)) FROM events WHERE run_id='X' ORDER BY rowid` |

---

## 结尾

**下一篇推荐：** → R5：CLI 参考

---

## 本文件说明

这是文档 `trajectory-events.md` 的细纲。实际写作时需与 `trajectory.py` 中每个 `emit()` 的 payload 字段逐一核对。

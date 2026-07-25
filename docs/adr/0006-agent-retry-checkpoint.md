---
status: proposed
date: 2026-07-21
---

# 0006: Agent 重试与检查点

## 背景

LLM 调用链（Loop → Backend → SDK → HTTP → API）有多个故障点：网络超时、限流、服务端瞬时故障、流中断。
当前 Loop 只 catch 异常后写 error + run_end 就退出，不做重试。同时进程崩溃后无法从已有轨迹恢复继续执行。

本 ADR 覆盖 Week 1 路线的"指数退避 + 抖动重试"和"检查点与回退"两项。

## Considered Options

| 决策点 | 选项 | 选出方案 |
|--------|------|----------|
| 重试所在层 | A: 仅 Backend 层 / B: 两层分工（SDK + Loop） | B |
| 流中断重试策略 | A: 续传 / B: 从头重试 | B（LLM 非确定性） |
| 响应分歧处理 | A: 接受新结果 / B: 检测并记录 | B |
| 检查点时刻 | A: 工具执行后 / B: 工具执行前（事务语义） | B |
| 恢复时 config 变化 | A: 拒绝 / B: 允许+告警 | B |
| retry 消融控制 | A: 硬编码 / B: AgentConfig 暴露 | B |

## 决策

### 1. 两层重试分工

| 层 | 职责 | 机制 | 对 Loop |
|----|------|------|---------|
| SDK 层 | 瞬时网络抖动 | `OpenAI(max_retries=2)` — SDK 自动重试 429/500/502/503/连接错误 | 完全透明 |
| Loop 层 | 策略级重试 | 指数退避 + 全抖动，retry budget 可控 | emit `retry` 事件入轨迹 |

**为什么两层不是一层**：SDK 层重试对 Loop 完全透明，基础设施噪声被吸收。Loop 层重试做策略决策——是否重试可由 AgentConfig 开/关（消融实验变量）、有上下文（当前 turn、剩余 budget）、厂商无关、可审计记录。

### 2. 异常分类

```
OpenAIError
├── APITimeoutError          ✅ 可重试（网络瞬时超时）
├── APIConnectionError       ✅ 可重试（DNS/TCP/TLS 瞬时失败）
└── APIStatusError
    ├── BadRequestError      (400) ❌ 请求格式错
    ├── AuthenticationError  (401) ❌ key 无效
    ├── PermissionDeniedError(403) ❌ 权限不足
    ├── NotFoundError        (404) ❌ 模型/端点不存在
    ├── UnprocessableEntityError(422) ❌ 参数语义问题
    ├── RateLimitError       (429) ✅ 可重试（限流，延迟后恢复）
    └── InternalServerError  (500+) ✅ 可重试（服务端瞬时故障）
```

额外纳入流中断异常（ADR-0005 `StreamDisconnect`）：✅ 可重试。

不可重试异常 + codec 异常（`ValueError` 等）→ 直接 `error(kind=...)` + `run_end`，行为与非流式一致。

### 3. 指数退避参数

| 参数 | 值 | 说明 |
|------|-----|------|
| base | 2s | 初始等待 |
| max_delay | 120s | 封顶等待 |
| max_retries | 5 | 最大尝试次数（不含首次） |
| jitter | full jitter | `random.uniform(0, min(max_delay, base * 2^attempt))` |

退避序列：2s → 4s → 8s → 16s → 32s → 64s → 120s → 120s...（第 5 次后 120s 封顶）。

`AgentConfig` 暴露：

```python
@dataclass
class AgentConfig:
    ...
    retry_enabled: bool = True
    retry_max_attempts: int = 5
    retry_base_s: float = 2.0
    retry_max_delay_s: float = 120.0
```

评测消融实验设 `retry_enabled=False` 可对比重试对通过率的贡献。

### 4. 重试行为规则

| 规则 | 说明 |
|------|------|
| 不消耗 turn | 重试在同一轮内循环，turn 不递增 |
| 流中断从头重试 | 废弃已缓冲区 delta，重新调用 `backend.stream()`——LLM 是概率模型，每次回答可能不同 |
| 不重置 budget | 同一轮内重试共享同一个 `max_turns` 计数和 token 累计 |
| retry budget 耗尽 | 全部重试耗尽 → emit `error(retry_exhausted, attempts=5)` → `run_end(llm_error)` |

### 5. 响应分歧检测

重试同一个 messages 列表，模型可能返回不同的结果（非确定性）：

- 比较两次响应的 `stop_reason` 和 `tool_use` 列表是否相同
- 不一致时 emit `retry_divergence` 事件，payload 含前后差异摘要
- 不影响主流程——接受新响应继续执行

### 6. 重试事件

每次重试 emit 一条 `retry` 事件：

```json
{"run_id": "...", "turn": 3, "ts": "...", "type": "retry",
 "payload": {"attempt": 2, "delay_s": 4.0, "reason": "rate_limit",
  "estimated_input_tokens": 5200}}
```

评测报告可统计：重试触发率、平均延迟、额外 token 成本。

### 7. 检查点时机——事务语义

检查点设在 LLM 响应之后、工具执行之前：

```
1. backend.complete() → ModelResponse
2. messages.append(resp.message)                ← assistant 消息已就位
3. traj.persist()                                ← 检查点
4. for block in tool_uses:
       handler(block.input) → tool_results
5. messages.append(Message(role="user", content=tool_results))
6. turn += 1
```

**恢复语义**：若进程在 4/5/6 崩溃，从检查点恢复后 assistant 消息中的 tool_use 都未执行过（步骤 3 时尚未执行）。这些 tool_use 全部重做——事务要么全成功，要么全回滚。

### 8. 恢复接口

新增 `Trajectory.from_db(run_id, db_path) → Trajectory` 类方法：

```python
@classmethod
def from_db(cls, run_id: str, db_path: str) -> Trajectory:
    conn = sqlite3.connect(db_path)
    config_json = conn.execute(
        "SELECT config_json FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    config = AgentConfig(**json.loads(config_json))
    traj = cls(run_id=run_id, config=config)
    for row in conn.execute(
        "SELECT turn, ts, type, payload FROM events WHERE run_id=? ORDER BY ts",
        (run_id,),
    ):
        traj.events.append(Event(
            run_id=run_id, turn=row[0], ts=row[1],
            type=EventType(row[2]), payload=json.loads(row[3]),
        ))
    traj._persisted_count = len(traj.events)
    return traj
```

Agent 新增 `resume()` 方法：

```python
def resume(self, traj: Trajectory) -> Trajectory:
    self._validate_consistent(traj)
    messages = traj.to_messages()
    turn = self._count_turns(traj)
    # 后续 loop 逻辑与非流式一致
```

不重放历史 LLM 调用——直接从 `to_messages()` 重建 messages，继续下一轮。

### 9. 恢复边界处理

| 场景 | 处理 |
|------|------|
| config.model 不一致 | ✅ 允许，emit warning |
| config.tools 不一致 | ❌ `ValueError`——tools 列表变化可能导致未知工具 |
| workdir 文件变化 | ✅ 文件不存在 → handler 返回 is_error → 模型自愈 |
| 跨会话污染 | ❌ 不可能——恢复按 `run_id` 隔离 |
| 已有 run_end | ❌ status 为 `end_turn/max_turns/llm_error` 等——不可恢复 |
| 无 run_end（进程崩溃） | ✅ status 为 `in_progress`——可恢复 |

### 10. Loop 重试流程（伪代码）

```python
def _attempt_with_retry(self, messages, tools, config) -> ModelResponse | Iterator[StreamEvent]:
    last_error = None
    for attempt in range(1 + (self.config.retry_max_attempts if self.config.retry_enabled else 0)):
        try:
            if attempt > 0:  # 重试
                delay = compute_delay(attempt)
                time.sleep(delay)
                traj.emit(EventType.retry, turn=turn, payload={...})
            if self.config.stream:
                return self._stream_from(backend, messages, tools, config)
            else:
                return backend.complete(messages, tools, config)
        except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError):
            last_error = e
            continue  # 可重试
        except (BadRequestError, AuthenticationError, ...):
            raise  # 不可重试，穿透
    # budget 耗尽
    traj.emit(EventType.error, kind="retry_exhausted", attempts=attempt)
    raise last_error
```

## Consequences

- 重试机制可控可消融——评测可关重试验证"重试对通过率的贡献"
- 每次重试独立入轨迹——可精确回答"多少任务触发过重试、平均延迟多少秒"
- 检查点使长任务可恢复——30+ 轮会话不会因进程崩溃全部丢失
- 恢复引入 `Trajectory.from_db()` 和 `Agent.resume()`，保持向后兼容（功能不修现有接口）
- SDK 层和 Loop 层重试分工明确，异常分类细化为 10 个细类 + 1 兜底

## 修正案（2026-07-23）

### 修正案 1：配置归属以 ADR-0005 为准

§3 的配置字段归属以 ADR-0005 §16 为准——retry/timeout 旋钮入 `TransportConfig`
（`timeout_s` / `retry_enabled` / `retry_max_attempts` / `retry_base_s` / `retry_max_delay_s`），
`AgentConfig` 不再持有 `turn_timeout_s`；消融经 `AgentConfig(transport=TransportConfig(retry_enabled=False))` 控制。
原文 §3 表格中 `AgentConfig` 字段表述作废。

### 修正案 2：retry budget 耗尽后保留精确终态

§4 retry budget 耗尽的终态修正——保留精确终态：
- last error 为 `APITimeoutError` 时 `run_end(llm_timeout)`
- 其余可重试错误耗尽 `run_end(llm_error)`

`error(kind=retry_exhausted)` 的 payload 携带 `last_error_kind`。
理由：轨迹是评测唯一数据源，失败分类信息不丢失。

### 修正案 3：工具执行 at-least-once 语义（已知限制）

检查点设在工具执行之前（§7），因此若进程在工具执行期间崩溃，该次崩溃前已执行的工具会因回滚到检查点而被重做。
这对有副作用（写文件、bash 命令）的工具意味着可能执行两次，属于 at-least-once 语义。
v0 不接受此限制的工程化缓解；这是 Temporal、Airflow 等生产系统的常见议题，面试时可展开讨论。

# Agent LLM 调用重试（ADR-0006 实现）

## 范围

**本次只实现 Loop 层 LLM 调用重试**（指数退避 + 全抖动，异常分类，事件入轨迹）。
ADR-0006 中的检查点/恢复拆为独立计划（`docs/plans/0004-checkpoint-resume.md`），后续提交。

## A. 批准锁定

### A-1. 配置归属：TransportConfig（对应 ADR-0005）

retry/timeout 旋钮归入 `TransportConfig`，不做在 `AgentConfig` 顶层。
ADR-0006 原表格作废，以 ADR-0005 §16 及本文为准。

```python
@dataclass
class TransportConfig:
    stream: bool = True
    timeout_s: float = 120.0
    retry_enabled: bool = True
    retry_max_attempts: int = 5
    retry_base_s: float = 2.0
    retry_max_delay_s: float = 120.0
```

`AgentConfig.turn_timeout_s` 移除；消融实验：`AgentConfig(transport=TransportConfig(retry_enabled=False))`。

### A-2. Retry 耗尽终态：保留精确分类

retry budget 耗尽后：

- last error 为 `APITimeoutError` → `run_end(llm_timeout)`
- 其余可重试错误耗尽 → `run_end(llm_error)`

error 事件 `kind=retry_exhausted` 的 payload 携带 `last_error_kind`。

## B. 架构

### 两层重试分工

| 层 | 职责 | SDK 配置 |
|---|------|---------|
| SDK 层 | 429/500/连接瞬时抖动 | `OpenAI(max_retries=2)`，对 Loop 完全透明 |
| Loop 层 | 策略级重试 | 指数退避 + 全抖动，retry budget 可控，emit 重试事件 |

## C. 文件改动

| 文件 | 改动 |
|------|------|
| `vague_code/agent/config.py` | `TransportConfig` 新增 timeout/retry 字段 + 校验；`AgentConfig` 移除 `turn_timeout_s` |
| `vague_code/agent/backend.py` | `OpenAI(max_retries=2)` |
| `vague_code/agent/trajectory.py` | `EventType` 新增 `retry` / `retry_divergence` |
| `vague_code/agent/ir.py` | 新增 `StreamDisconnect` 异常 |
| `vague_code/agent/retry.py` | 新建：`RetryDecision`, `RetryPolicy`, `classify_llm_error`, `estimate_input_tokens`, `response_signature` |
| `vague_code/cli/__init__.py` | `timeout_s` 改读 `config.transport.timeout_s`；新增 `--no-retry` / `--retry-*` 参数 |
| `tests/test_retry.py` | 新建 |
| `tests/test_agent_loop.py` | 适配 config 变化，异常注入测试设 `retry_enabled=False` |

## D. RetryNotice（CLI 实时提示）

扩展 ADR-0005 的 StreamEvent 联合类型，增加第 10 种 `RetryNotice`：

```python
@dataclass
class RetryNotice:
    attempt: int
    delay_s: float
    reason: str
    def to_dict(self) -> dict -> {"stream_type": "retry_notice", ...}
```

- 只经 RunHandle 实时 yield，**不以 stream_event 落盘**（轨迹事实记录走 `retry` 事件）。
- `StreamEventVisitor` + `dispatch_event` + `NullVisitor` 各加一支。
- `RichStreamVisitor.retry_notice`: `⚠ 请求失败（{reason}），{delay_s}s 后重试（第 {attempt} 次）`

## E. Loop 集成（待 Commit 2）

```
while turn < max_turns:
    retry_index = 0
    while True:
        aggregator = _StreamAggregator()
        buffered = []
        try:
            for ev in self._stream_from(...):
                buffered.append((ts, ev)); aggregator.feed(ev); yield ev
            resp = aggregator.result(message_end)
        except Exception as e:
            decision = classify_llm_error(e)
            if not retryable or exhausted:
                error(...) + run_end(...); return
            delay = policy.delay(retry_index)
            traj.emit(retry, ...) + yield RetryNotice(...)
            time.sleep(delay); retry_index++; continue
        for ts, ev in buffered: traj.emit(stream_event, ..., ts=ts)
        llm_response(...); handle stop_reason; break
```

### 检查点关联（未来 Commit 3）

只被接受的 `resp` 会走到 `messages.append(resp.message)`，检查点应插在该行之后、工具执行之前。失败 attempt 不污染 messages。

## F. 已知限制与改进项（计划尾部）

- **同步 sleep**：v0 `time.sleep(delay)` 正确；若未来 Loop 异步化（Week 2 工具并发调度），必须换 `asyncio.sleep`，否则阻塞事件循环。
- **无总时长预算**：重试只有次数预算，最坏情况 Loop 层约 62s 延迟 + SDK 重试多层叠加。改进项：`max_retry_duration_s`（总重试时长上限，v0 不做）。
- **RetryNotice 不对称性**：RunHandle 实时 yield RetryNotice；`Agent.run()` 静默 drain，harness 零感知。

## G. 测试计划

1. `RetryPolicy.delay`：cap 序列 `[2,4,8,16,32,64,120,120]`；full jitter 可零；config 校验。
2. `classify_llm_error`：10+ 异常变体，断言 retryable/reason/error_kind/terminal_reason。
3. `estimate_input_tokens` / `response_signature`：边界 + tool id 无关性。
4. 现有 loop 异常注入测试：设为 `retry_enabled=False` 保持原断言。

## H. 提交切分

```
Commit 1 (this PR):  feat(agent): add transport retry config and retry primitives
  - TransportConfig 迁移 + backend max_retries=2 + CLI flags
  - retry.py 纯函数 + EventType 扩展 + StreamDisconnect + test_retry.py
  - 测试适配
  - docs/plans/0003-agent-retry.md
  - ADR-0006 修正案 1（配置归属）

Commit 2 (next):     feat(agent): retry LLM calls in loop with live retry notice
  - Loop attempt 循环 + stream buffer discard + Trajectory.emit(ts=)
  - RetryNotice (StreamEvent #10) + visitor/renderer
  - 行为测试
  - ADR-0006 修正案 2（终态精度）+ ADR-0005 修正案（RetryNotice）

Commit 3 (later):    feat(agent): checkpoint and resume (ADR-0006 §7-§9)
  - 略
```

## I. 验证

```bash
uv run ruff check src tests
uv run mypy src tests
uv run pytest tests/ -v
```

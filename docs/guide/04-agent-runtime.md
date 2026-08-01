# 细纲：04-agent-runtime.md

**预估行数：** ~500 行
**定位：** 专题展开 Agent 运行时核心。

---

## 开头

- **谁需要读：** 想理解 Agent 核心循环内部机制的开发者
- **前置阅读：** 03-a-single-turn-explained.md（理解一次循环的完整概览）
- **读完能做什么：** 能独立阅读 `loop.py` 源码，理解重试/checkpoint/resume 的完整流程

---

## 细纲

### 1. 概述（~30 行）

- Agent Runtime 的定位：连接所有子系统的中央引擎
- 一句话公式：`Agent(config).run(task, workdir) → Trajectory`
- 库优先设计（ADR-0001）：CLI/TUI/Eval 共享同一编程接口
- 三个核心设计约束：零 asyncio、同步阻塞、迭代器流式、纯函数子系统

### 2. Agent 类结构（~50 行）

| 方法 | 代码位置 | 职责 | 调用方 |
|------|---------|------|--------|
| `__init__()` | `loop.py:160-184` | 注入 config/backend/tools/memory_store/permission hooks，校验 registry key | CLI/TUI/Eval |
| `run()` | `loop.py:186-190` | 便捷入口，内部调用 start() 并 exhaust RunHandle | 评测 harness |
| `start()` | `loop.py:192-239` | 构造 Trajectory、SystemPrompt、messages 初始化、repo map 注入、dynamic tool 注入 | CLI/TUI |
| `_run_gen()` | `loop.py:241-489` | 生成器主循环（内部方法，从 start() 调用） | start() |
| `resume()` | `loop.py:569-606` | 从 Trajectory 恢复运行 | TUI 侧边栏、CLI --resume |
| `_checkpoint()` | `loop.py:491-496` | 调用 `Trajectory.persist()` 增量写入 | _run_gen / resume |
| `_check_tool_permission()` | `loop.py:498-541` | 单工具权限校验 | _run_gen / _execute_pending_tools |
| `_execute_pending_tools()` | `loop.py:627-680` | resume 时回放未执行工具 | resume() |
| `_stream_from()` | `loop.py:682-710` | 统一流式/非流式 LLM 调用接口 | _run_gen |
| `_persist()` | `loop.py:712-730` | 最终 persist + 失败恢复 | _run_gen finally |

### 3. ReAct 循环详解（~80 行）

**循环结构概要（对应 03 步骤 3-9）：**
```
while turn_box[0] < config.max_turns:
    1. compress_chain()       # 压缩（可选）
    2. backend.stream()       # LLM 调用
    3. parse StopReason       # 分支
    4. if tool_use:           # 工具执行
         a. permission check  # 每工具
         b. execute tools     # 并发或串行
         c. append results    # 更新 messages
         d. checkpoint        # 增量 persist
         e. turn_box += 1     # 下一轮
    5. if end_turn: return
```

**`turn_box` 设计（`loop.py:245`）：**
- `list[int]` 作为可变引用传递（`loop.py:238`）
- 跨 `resume()` 传递，`_run_gen()` 内部通过 `turn_box[0]` 读写
- 为什么用 `list[int]` 而不是 `int`：Python 的 int 不可变，list 可原地修改

**ASCII 状态转换图：**
```
                    ┌──────────────────────┐
                    │   Agent.start()      │
                    │   (初始化/注入/规则)  │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   Turn N 开始        │
                    │   emit(turn_start)   │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   compress_chain()   │ ←─── 纯函数，可选
                    │   emit(compression)  │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   backend.stream()   │ ←─── LLM 调用
                    │   _StreamAggregator   │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │   Parse StopReason   │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼──────────────────┐
              │                │                  │
              ▼                ▼                  ▼
     ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
     │ tool_use     │  │ end_turn     │  │ max_tokens   │
     │              │  │ /stop_seq    │  │ /others      │
     ▼              ▼  ▼              ▼  ▼              ▼
 ┌──────────────────┐  ┌──────────────┐  ┌──────────────┐
 │[pre-pass 权限]   │  │ emit(run_end)│  │ emit(run_end)│
 │ 允许→执行       │  │ → 返回       │  │ → 返回       │
 │ 拒绝→ToolResult  │  └──────────────┘  └──────────────┘
 │(is_error=True)   │
 │                  │
 │[并发/串行执行]   │
 │  → emit(tool_call)│
 │  → emit(tool_res)│
 │                  │
 │[checkpoint]      │
 │[turn_box += 1]   │
 │ → 回到 Turn N+1  │
 └──────────────────┘
```

**循环终止条件汇总：**
| 条件 | 行为 | 代码位置 |
|------|------|---------|
| `end_turn` / `stop_sequence` | `emit(run_end, "end_turn")` 正常返回 | `loop.py:388-390` |
| `max_tokens` / `content_filter` / `unknown` | `emit(run_end, 原因)` 异常结束 | `loop.py:392-394` |
| `turn == max_turns` | 记录 pending tool calls，结束 | `loop.py:402-407` |
| 重试耗尽 | `emit(run_end, "retry_exhausted")` | `loop.py:348-355` |
| 致命错误（binding 失败等） | `emit(run_end, "tool_bind_error")` 立即结束 | `loop.py:220-224` |

### 4. RunHandle 模式（~40 行）

**代码位置：** `loop.py:119-154`

**设计意图：** 迭代器 + 上下文管理器 + trajectory 属性

**使用模式：**
```python
# 模式 1：迭代器
handle = agent.start(task, workdir)
for ev in handle:
    render(ev)
traj = handle.trajectory   # 只有 exhaust 后可用

# 模式 2：上下文管理器
with agent.start(task, workdir) as handle:
    for ev in handle:
        render(ev)
    traj = handle.trajectory

# 模式 3：一键运行（评测 harness 使用）
traj = agent.run(task, workdir)   # 内部 exhaust handle
```

**关键属性：**
- `__next__()` → `StreamEvent`（生成器驱动）
- `close()` → 停止生成器（`worker.is_cancelled` → `handle.close()`，`app.py:115-117`）
- `__enter__` / `__exit__` → 自动 `close()`
- `.trajectory` → 只能读一次（generator exhaust 后）

### 5. _StreamAggregator 工作原理（~50 行）

**代码位置：** `loop.py:51-114`

**状态机设计：**
- 四种 buffer，各司其职：

| buffer | 类型 | 用途 | 对应 StreamEvent |
|--------|------|------|-----------------|
| `_text` | StringIO | 累积文本输出 | TextDelta |
| `_thinking` | StringIO | 累积推理过程 | ThinkingDelta |
| `_tool_buffers` | `dict[str, StringIO]` | 按 tool_use_id 累积参数 | ArgsDelta |
| `_tool_names` | `dict[str, str]` | tool_use_id → tool name 映射 | ToolUseStart |

**feed() 事件分发映射：**

| StreamEvent | 操作 | 代码位置 |
|-------------|------|---------|
| TextDelta | `_text.write(delta)` | `loop.py:64-65` |
| ThinkingStart | 无操作 | `loop.py:66-67` |
| ThinkingDelta | `_thinking.write(delta)` | `loop.py:68-69` |
| ThinkingEnd | 记录 signature | `loop.py:70-71` |
| ToolUseStart | 初始化 new StringIO for this id | `loop.py:72-79` |
| ArgsDelta | `_tool_buffers[id].write(delta)` | `loop.py:81-85` |
| ToolUseEnd | 无操作 | `loop.py:86-87` |
| MessageEnd | 触发 `result()` 组装 | `loop.py:88-90` |

**result() 组装逻辑（`loop.py:92-114`）：**
1. ThinkingBlock（如果有 thinking text + signature）
2. TextBlock（如果有 text）
3. ToolUseBlock（按 `_tool_order` 保序，遍历 `_tool_buffers`）
4. 空 blocks → 追加空 TextBlock（防 `message.content` 为空）

**边界处理：** 重复 ToolUseStart id → 警告并 reset buffer（`loop.py:74-76`）

### 6. 重试系统（~70 行）

**代码位置：** `retry.py` + `loop.py:331-376`

**`RetryPolicy` 配置（`retry.py:52-72`）：**
| 字段 | 默认值 | 说明 |
|------|--------|------|
| enabled | True | 是否启用重试 |
| max_attempts | 5 | 最大重试次数（0 = 不重试） |
| base_s | 2.0 | 指数退避基数（秒） |
| max_delay_s | 120.0 | 最大退避间隔 |

**退避公式：** `delay = random.uniform(0, min(max_delay_s, base_s × 2^retry_index))`（`retry.py:70-72`）

**错误分类表（`retry.py:75-124` `classify_llm_error()`）：**

| 异常类 | retryable | error_kind | 终端原因 |
|--------|-----------|------------|---------|
| `APITimeoutError` | true | `llm_timeout` | `llm_timeout` |
| `APIConnectionError` | true | `connection_error` | `llm_error` |
| `RateLimitError` | true | `rate_limit` | `llm_error` |
| `InternalServerError` | true | `server_error` | `llm_error` |
| `StreamDisconnect` | true | `stream_disconnect` | `llm_error` |
| Anthropic `APIConnectionError` | true | `llm_error` | `llm_error` |
| Anthropic `RateLimitError` | true | `rate_limit` | `llm_error` |
| `BadRequestError` / `AuthenticationError` / `PermissionDeniedError` / `NotFoundError` / `UnprocessableEntityError` | false | `llm_error` | `llm_error` |
| `ValueError` / `TypeError` | false | `codec_error` | `llm_error` |

**重试循环（`loop.py:331-376`）：**
```
while True:
    aggregator = _StreamAggregator()
    try:
        for ev in backend.stream(messages, tools, config):
            buffered.append(ev)          # 保留备份
            aggregator.feed(ev)
            yield ev                     # 流式输出
        resp = aggregator.result(message_end)
    except Exception as e:
        decision = classify_llm_error(e)
        if not decision.retryable or not policy.enabled or retry_index >= max_attempts:
            emit(run_end, ...)          # 耗尽
            return
        delay = policy.delay(retry_index)
        emit(retry, ...)                # 记录重试事件
        yield RetryNotice(...)           # 通知渲染器
        time.sleep(delay)
        retry_index += 1
        continue                         # 重新请求
    break
```

**重试事件记录：** `loop.py:362-368` emit 包含 attempt、delay_s、reason、exception 类名、estimated_input_tokens

### 7. Checkpoint/Resume（~60 行）

**两处 persist 点（`loop.py:410,485`）：**
| 时机 | 保证 | 风险 |
|------|------|------|
| LLM 响应后 | LLM output 已落盘 | 工具执行可能失败 |
| 工具执行后 | 本轮完整状态已落盘 | 下次 persist 前崩溃可能丢数据 |

**`Trajectory.persist()`（`trajectory.py:257-281`）：**
- SQLite WAL 模式 + 5 秒 busy_timeout
- 增量写入：`new_events = events[persisted_count:]`（`trajectory.py:269`）
- Runs 表 INSERT OR REPLACE
- Events 表 executemany

**Resume 完整流程（`loop.py:569-606`）：**
```
1. 检查轨迹是否已完成（有 run_end 事件）
   → 如果已经完成，直接返回（幂等）
2. _validate_consistent(traj)
   → 对比 stored config 和当前的 model/tools 一致性
   → 不匹配：警告（model 不同）或报错（tools 减少）
3. from_db(run_id, db_path)
   → 从 SQLite 重建 AgentConfig + 事件列表
4. to_messages()
   → 事件流 → messages 数组
5. 分析最后一条 llm_response 的 stop_reason
   → terminal（end_turn/stop_sequence/max_tokens/...）→ 结束
   → tool_use →
     a. 检查 max_turns → 结束
     b. _execute_pending_tools() → 回放未执行的工具调用
6. 计算 next_turn = T + 1
7. 恢复 _run_gen(traj, messages, [next_turn], bound_tools)
```

**`_validate_consistent()`（`loop.py:608-625`）：**
- 从 run_start event 中读取存储的 config
- 对比 model：不同则警告
- 对比 tools：当前工具集必须包含存储时的所有工具（子集校验）

**`_execute_pending_tools()`（`loop.py:627-680`）：**
- 查找 last messages[-1]（assistant）中的 ToolUseBlock
- 过滤出还没有对应 tool_result event 的
- 逐个执行（带权限检查，`check_confirm=False` 不弹窗）

**Persist 失败恢复（`loop.py:712-730 `）：**
- persist 失败 → 尝试 export JSONL 到 `{db_path}.{run_id}.recovery.jsonl`
- `parent.mkdir(parents=True, exist_ok=True)` 自动创建目录
- 最后检查是否有 run_end → 没有则追加一个

### 8. _stream_from 适配器（~30 行）

**代码位置：** `loop.py:682-710`

**统一流式/非流式接口：**
```python
def _stream_from(self, messages, tools, config):
    if config.get("stream") and hasattr(self.backend, "stream"):
        yield from self.backend.stream(messages, tools, config)
    else:
        resp = self.backend.complete(messages, tools, config)
        yield MessageStart(model=...)
        for block in resp.message.content:
            if TextBlock: yield TextDelta(...)
            elif ThinkingBlock: yield ThinkingStart + ThinkingDelta + ThinkingEnd
            elif ToolUseBlock: yield ToolUseStart + ArgsDelta + ToolUseEnd
        yield MessageEnd(stop_reason=..., usage=...)
```

### 9. 回调钩子（~30 行）

| 回调 | 类型 | 注册位置 | 用途 | TUI 桥接 |
|------|------|---------|------|---------|
| `_on_permission` | `Callable[[Operation, Decision], Decision]` | `loop.py:168` | 权限交互确认 | `app.py:142-162`（`asyncio.run_coroutine_threadsafe` + `push_screen_wait`） |
| `on_tool_result` | `Callable[[str, str, bool], None]` | `loop.py:169` | 工具结果流式更新 | `app.py:164-173`（`call_from_thread`） |
| `on_state_change` | `Callable[[str, dict], None]` | `loop.py:170` | 状态栏刷新 | `app.py:175-193`（turn/token/compression 更新） |

---

## 结尾

**下一篇推荐：** → 05-tool-system.md（专题展开 8 个工具的设计）
**相关 ADR：** 0001（Agent 即库）、0006（Retry + Checkpoint）
**相关 plans：** 0002（agent-loop）、0003-agent-retry（重试系统）

---

## 本文件说明

这是文档 `04-agent-runtime.md` 的细纲（大纲）。写作时所有 `loop.py` 行号需与最终代码一一核实。回调部分需同步检查 `app.py` 和 `harness.py` 中 _on_permission 的桥接逻辑。

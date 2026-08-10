# A Single Turn Explained

**谁需要读：** 想读懂 `loop.py` 源码的开发者
**前置阅读：** 02-architecture-overview.md（掌握子系统划分）
**读完能做什么：** 理解一次 Agent 循环中每个函数调用、每个数据变换的完整路径

---

## 步骤 1：前情提要——初始状态

一次运行从 `Agent.start(task, workdir)` 开始（`loop.py:186-238`）。此时内存中的初始状态是这样的：

```
messages = [
    Message("system", SystemPrompt.build()),
    Message("user", "请修一下 stats.py 的除零 bug"),
]
turn_box = [0]          # 可变引用，跨 turn 共享
config = AgentConfig(
    model="deepseek-v4-flash",
    max_turns=20,
    ...
)
```

**SystemPrompt.build() 的三段结构**（`context.py:9-30`）：

1. `AGENT_IDENTITY` — 行为守则常量："先读后改""改完要测""用 glob/grep 探索"
2. 规则文件内容 — `load_rules()` 从 `.agent/rules.md` 向上遍历目录树加载
3. 工作目录信息 — `工作目录根路径: {path}`

**Repo map 注入**（`loop.py:200-215`）：
`RepoIndex.build()` 构建 tree-sitter 符号索引，`to_map_text()` 生成符号地图追加为 `## 代码库符号地图` 区块到 system prompt。

**初始 Trajectory 事件**（`loop.py:210-216`）：
`emit(run_start)` 记录 task、workdir、system_prompt、config 快照、工具列表。这是轨迹的第一条事件。

**输出：** `RunHandle` 迭代器就绪

---

## 步骤 2：构建系统提示

**代码位置：** `context.py:9-30` `SystemPrompt.build()`

**输入：** 工作目录路径
**输出：** 完整的 system_prompt 字符串

三段结构详解：

| 段 | 来源 | 代码位置 | 备注 |
|---|------|---------|------|
| 身份标识 | `AGENT_IDENTITY` 常量 | `context.py:10-16` | "先读后改""改完要测""用 glob/grep 探索" |
| 项目规则 | `load_rules()` | `context_rules.py:19-40` | 向上遍历目录，最大 10KB/文件、最深 50 层 |
| 工作目录 | `Self._workdir` | `context.py:29` | 绝对路径 |

**数据变换：** `rules_paths → text content → concatenation`——收集规则文件路径 → 读取内容 → 拼接到 system prompt

---

## 步骤 3：压缩流水线

**代码位置：** `context_compress.py:489-526` `compress_chain()` → `loop.py:263-319`

**输入：** `messages: list[Message]` + `cfg: CompressionConfig`
**输出：** `(messages: list[Message], reports: list[LayerReport])`

压缩流水线有 4 层，逐层尝试回收 token 空间：

```
budget = compute_budget(model)         # 模型窗口 × 0.9
total = count_tokens(messages)         # 当前总用量

if not cfg.enabled → 直通，不压缩

无条件 → stale_snip()                    # 零 LLM 成本，纯规则
if total > budget * threshold1 → microcompact()
if total > budget * threshold2 AND backend → auto_compact()
if total > budget → truncate()
```

每层的输入、输出和副作用：

| 层 | 输入 | 输出 | 副作用 | 代码位置 |
|---|------|------|--------|---------|
| stale_snip | messages | messages（部分 ToolResultBlock 替换为 stale 标记） | 无（纯函数） | `context_compress.py:69-136` |
| microcompact | messages | messages（超长 ToolResultBlock 头尾折叠） | 无（纯函数） | `context_compress.py:151-208` |
| auto_compact | messages + backend | messages（旧轮次替换为 LLM 摘要） | `memory_store.ingest()` 蒸馏 | `context_compress.py:221-351` |
| truncate | messages + budget | messages（丢弃中间消息） | 无（纯函数） | `context_compress.py:356-484` |

每层产出 `LayerReport：{layer, before_tokens, after_tokens, affected}`，记录为 `EventType.compression` 事件（`loop.py:288-307`）。压缩整体异常时降级到 truncate 兜底（`loop.py:278-285`）。

---

## 步骤 4：LLM 调用

**代码位置：** `loop.py:682-710` `_stream_from()` + `loop.py:331-376` 内层循环

vague-code 支持流式和非流式两种模式：

```
config.transport.stream=True  → backend.stream()  → SSE chunks → StreamEvent
config.transport.stream=False → backend.complete() → 手动构造 StreamEvent 序列
```

**完整链路：**
```
IR messages → encode_request() → API HTTP request → SSE chunks → decode_chunk() → StreamEvent
```

**DeepSeek codec 流式解码**（`codecs/deepseek.py:247-379`）：
- 状态机 `DeepSeekStreamDecoder.decode_chunk()` 处理 SSE 行
- thinking 边界推断：`reasoning_content` 出现 → ThinkingStart，消失 → ThinkingEnd
- tool_call index→id 映射：SSE 中先发 index 后发 id
- finish + usage 延迟发射，等 usage chunk 到达

**Anthropic codec 流式解码**（`codecs/anthropic.py:218-334`）：
- 按 event type 分发：`content_block_start/stop/delta` → ThinkingStart/ToolUseStart/TextDelta
- signature_delta → ThinkingEnd 时组装 signature
- 过滤 `SKIP_BLOCK_TYPES` 中不需要暴露给上层的块

**_StreamAggregator**（`loop.py:51-114`）：
- 四种 buffer：`_text`、`_thinking`、`_tool_buffers`（按 id 的 StringIO）、`_tool_names`
- `result(message_end)` 组装：ThinkingBlock → TextBlock → ToolUseBlock（按 tool_order 保序）
- 空 blocks 时追加空 TextBlock 防止下游处理挂起

---

## 步骤 5：解析停止原因

**代码位置：** `loop.py:388-394`

LLM 返回的 `stop_reason` 决定了下一步路径：

| StopReason | 含义 | 下一步行为 | 对应厂商 finish_reason |
|------------|------|-----------|----------------------|
| `end_turn` | 正常结束 | `run_end()` | OpenAI `"stop"` / Anthropic `"end_turn"` |
| `max_tokens` | Token 用完 | `run_end()` | OpenAI `"length"` / Anthropic `"max_tokens"` |
| `stop_sequence` | 命中停止序列 | `run_end()` | Anthropic `"stop_sequence"` |
| `tool_use` | 请求工具调用 | 进入工具执行阶段 | OpenAI `"tool_calls"` / Anthropic `"tool_use"` |
| `content_filter` | 内容被过滤 | `run_end()` | OpenAI `"content_filter"` / Anthropic `"refusal"` |

`stop_sequence` 与 `end_turn` 同归为正常结束。关键分支：`tool_use` 会进入步骤 6-7 的工具执行管道，其他所有原因直接结束本轮。

---

## 步骤 6：权限检查

**代码位置：** `loop.py:498-541` `_check_tool_permission()` + `permission.py:135-160` `evaluate()`

**输入：** `ToolUseBlock` + `PermissionMode` + 用户规则列表
**输出：** `(decision: Decision, content: str, is_error: bool)`

决策流程三步走：

```
1. rules 匹配 — 遍历用户规则列表，pattern 匹配则返回 rule.action
   → DENY 最高优先级
2. 默认策略表 — 根据工具分类查表
3. 返回 ALLOW / CONFIRM / DENY
```

4 种模式的默认策略：

| 操作类别 | safe | normal | autoedit | auto |
|---------|------|--------|----------|------|
| read（读文件/搜索） | ALLOW | ALLOW | ALLOW | ALLOW |
| write（写文件/patch） | DENY | CONFIRM | ALLOW | ALLOW |
| bash_safe（ls/echo/...） | DENY | CONFIRM | CONFIRM | ALLOW |
| bash_dangerous（rm/curl\|sh/...） | DENY | CONFIRM | CONFIRM | CONFIRM |

bash 命令分为 18 个安全命令（ls、git 操作、cat、head、echo、pwd、which...）和 24 个危险命令（rm、dd、chmod、kill、curl|sh、bash -c、sed -i...）（`permission.py:42-87`）。

**CONFIRM 分支：** 回调 `_on_permission(op, decision)` → TUI 弹窗交互（`app.py:_thread_permission` + `screens/permission.py`；`write_file`/`patch` 先展示写入前 diff 预览，拒绝理由经 `op.feedback` 回传模型）。无回调时默认 DENY（CLI 模式）。每次决策 emit `EventType.permission_check` 写入审计日志（`loop.py:519-522`）。

---

## 步骤 7：工具执行

**代码位置：** `loop.py:438-481`

工具执行选择并发或串行路径：

```
concurrent_tools=True 且 tool_uses > 1 → execute_concurrent()
否则 → 逐个串行执行
```

### 并发路径

**1. ResourceScope 提取**（`concurrency.py:54-85`）：

每个工具在调用时提取三维 scope：

| 工具 | op_type | scope_type | path 来源 |
|------|---------|------------|----------|
| read_file | READ | EXACT | `input["path"]` |
| write_file（文件存在） | WRITE | EXACT | `input["path"]` |
| write_file（新文件） | STRUCTURAL_WRITE | EXACT | `input["path"]` |
| patch | WRITE | EXACT | `input["path"]` |
| glob | READ | PREFIX | pattern 目录前缀 |
| grep | READ | PREFIX | `input["path"]` |
| bash | WRITE | WORKSPACE | — |
| memory_search | READ | WORKSPACE | — |
| code_search | READ | EXACT | `input["path"]`（可选过滤） |

**2. 冲突检测**（`concurrency.py:90-104`）：
- 都是 READ → 不冲突
- 任一为 WORKSPACE → 冲突
- path 相同 → 冲突
- A 为 PREFIX 且 B.path 在 A.path 下 → 冲突
- 否则 → 不冲突

**3. 分组调度**（`concurrency.py:118-138`）：
贪心算法：每个 tool call 尝试加入第一个无冲突的现有组；无合适组则新建。组内并发，组间串行。

**4. 并发执行**（`concurrency.py:146-212`）：
`ThreadPoolExecutor(max_workers=min(len(group), 4))`，120 秒超时。失败传播：组 N 中任何工具失败 → 组 N 及后续组全部标记 `[已跳过]`。

### 串行路径

逐个调用 `handler(block.input)`。异常捕获：`PermissionError`、`FileNotFoundError`、`ValueError` 转为 `ToolResultBlock(is_error=True)`。

### 公共逻辑

- 未知工具 → 返回 `"Unknown tool: {block.name}"`
- 单工具输出 50K 截断（`loop.py:558-565`）
- 更新 messages：`messages.append(resp.message)` + `messages.append(Message(role="user", content=tool_results))`（`loop.py:409,483`）

---

## 步骤 8：Checkpoint

**代码位置：** `loop.py:491-496` `_checkpoint()`

两处 persist 点，覆盖本轮完整状态：
1. LLM 响应后（`loop.py:410`）：保存 LLM 输出结果
2. 工具执行后（`loop.py:485`）：保存工具执行结果

**Trajectory.persist()**（`trajectory.py:257-281`）：
- WAL 模式 + 5 秒忙等超时
- 增量写入：只写 `_persisted_count` 之后的新事件
- `runs` 表 INSERT OR REPLACE（幂等）
- `events` 表 executemany 批量写入

---

## 步骤 9：下一轮

**代码位置：** `loop.py:484` `turn_box[0] += 1`

循环条件：`while turn_box[0] < self.config.max_turns`（`loop.py:250`）

终止条件汇总：

| 触发点 | 代码位置 | 描述 |
|--------|---------|------|
| `end_turn` / `stop_sequence` | `loop.py:388-390` | 正常完成 |
| `max_tokens` / `content_filter` | `loop.py:392-394` | 异常完成 |
| `max_turns` 熔断 | `loop.py:420-425` | 最后一轮 LLM 请求工具被切，pending tool calls 被记录，`run_end(max_turns, pending=n)` |
| `max_turns` 兜底 | `loop.py:505` | while 正常退出后的防御性兜底，正常流程不可达（见 known-issues U3） |
| 重试耗尽 | `loop.py:348-355` | LLM 永久性失败 |
| 工具绑定/并发异常 | `loop.py:220-224, 444-447` | 紧急停止 |

> **注意**：`max_turns` 是"硬墙"而非可正常耗尽的预算。续轮唯一途径是 `tool_use`，而它在最后一轮必被 `loop.py:420` 熔断，`turn_box` 永远停在 `max_turns - 1`，故 `loop.py:505` 的兜底在正常流程中不可达（防御性死代码，见 `docs/known-issues.md` U3）。

---

## 步骤 10：完整调用图

```
Agent.start(task, workdir)
 │
 ├─ SystemPrompt.build()              ─── context.py:9-30
 ├─ RepoIndex.build() + to_map_text() ─── loop.py:200-215
 ├─ emit(run_start)                   ─── loop.py:210-216
 │
 └─ RunHandle ← _run_gen()
     │
     while turn < max_turns:
     │
     ├─ compress_chain()              ─── context_compress.py:489-526
     │   ├─ stale_snip()              ─── context_compress.py:69-136
     │   ├─ microcompact()            ─── context_compress.py:151-208
     │   ├─ auto_compact()            ─── context_compress.py:221-351
     │   └─ truncate()                ─── context_compress.py:356-484
     │
     ├─ backend.stream()              ─── loop.py:337 (backend.py:68-91)
     │   ├─ DeepSeekStreamDecoder     ─── codecs/deepseek.py:247-379
     │   └─ _StreamAggregator         ─── loop.py:51-114
     │
     ├─ parse StopReason              ─── loop.py:388-394
     │
     ├─ [tool_use] →
     │   ├─ _check_tool_permission()  ─── loop.py:498-541 (permission.py:135-160)
     │   ├─ execute_concurrent()      ─── concurrency.py:146-212
     │   │   ├─ _extract_scope()      ─── concurrency.py:54-85
     │   │   ├─ schedule()            ─── concurrency.py:118-138
     │   │   └─ ThreadPoolExecutor    ─── concurrency.py:171
     │   ├─ _truncate_tool_content()  ─── loop.py:558-565
     │   ├─ _checkpoint()             ─── loop.py:491-496
     │   └─ turn_box[0] += 1          ─── loop.py:484
     │
     ├─ [end_turn/其他] →
     │   └─ emit(run_end)             ─── loop.py:389-394
     │
     └─ _persist(traj)                ─── loop.py:489 (trajectory.py:712-730)
```

---

## 下一篇

→ **04-agent-runtime.md**：专题展开 Agent 类结构、ReAct 循环、重试/检查点/恢复。

**相关 ADR：** 0001（Agent 即库）、0006（Retry + Checkpoint）
**相关 plans：** 0002（agent-loop）、0003（重试系统）

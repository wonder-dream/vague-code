# 细纲：03-a-single-turn-explained.md

**预估行数：** ~600 行（含标注调用图）
**定位：** 把一次 Agent 循环展开到每个函数调用、每个数据变换。

---

## 开头

- **谁需要读：** 想读懂 `loop.py` 源码的开发者
- **前置阅读：** 02-architecture-overview.md（掌握子系统划分）
- **读完能做什么：** 理解一次 Agent 循环中每个函数调用、每个数据变换的完整路径

---

## 细纲（10 个步骤，每个步骤包含：代码位置、输入、输出、数据变换）

### 步骤 1：前情提要——初始状态（~50 行）

**代码位置：** `loop.py:186-238` `Agent.start()`

**初始状态图示：**
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

**`SystemPrompt.build()` 的三段结构（`context.py:9-30`）：**
1. `AGENT_IDENTITY` — 行为守则（"先读后改""改完要测"）
2. 规则文件内容（`load_rules()` 从 `.agent/rules.md` 向上遍历加载）
3. `Workspace root: {path}`

**repo map 注入（`loop.py:200-215`）：**
- `RepoIndex.build()` → 追加 `## 代码库符号地图` 区块到 system_prompt

**初始 Trajectory 事件（`loop.py:210-216`）：**
- `emit(run_start)` — 含 task、workdir、system_prompt、config 快照、工具列表

**输出：** `RunHandle` 迭代器就绪

### 步骤 2：构建系统提示（~40 行）

**代码位置：** `context.py:9-30` `SystemPrompt.build()`

**输入：** 工作目录路径（`str | Path`）
**输出：** 完整的 system_prompt 字符串

**三段结构详解：**

| 段 | 来源 | 代码位置 | 备注 |
|---|------|---------|------|
| 身份标识 | `context.py:10-16` `AGENT_IDENTITY` | 硬编码常量 | "先读后改""改完要测""用 glob/grep 探索" |
| 项目规则 | `context_rules.py:19-40` `load_rules()` | 向上遍历目录 | 最大 10KB/文件、最多 20 个、最深 50 层 |
| 工作目录 | `context.py:29` | `Self._workdir` | 绝对路径 |

**数据变换：** `rules_paths → text content → concatenation`

### 步骤 3：压缩流水线（~80 行）

**代码位置：** `context_compress.py:489-526` `compress_chain()` → `loop.py:263-319`

**输入：** `messages: list[Message]` + `cfg: CompressionConfig`
**输出：** `(messages: list[Message], reports: list[LayerReport])`

**触发门控流程：**
```
budget = compute_budget(model)         # context_tokens.py:131
total = count_tokens(messages, ...)    # context_tokens.py:47

if not cfg.enabled → 直通，不压缩

无条件 → stale_snip()                    # 零 LLM 成本
if total > budget * microcompact_threshold → microcompact()
if total > budget * auto_compact_threshold AND backend → auto_compact()
if total > budget → truncate()
```

**每层输入/输出/副作用表：**

| 层 | 输入 | 输出 | 副作用 | 代码位置 |
|---|------|------|--------|---------|
| stale_snip | messages | messages（部分 ToolResultBlock 替换为 stale 占位符） | 无（纯函数） | `context_compress.py:69-136` |
| microcompact | messages | messages（超长 ToolResultBlock 折叠） | 无（纯函数） | `context_compress.py:151-208` |
| auto_compact | messages + backend | messages（旧轮次替换为摘要） | `memory_store.ingest()` 蒸馏 | `context_compress.py:221-351` |
| truncate | messages + budget | messages（丢弃中间消息） | 无（纯函数） | `context_compress.py:356-484` |

**LayerReport 可观测性：** 每层产出 `(layer, before_tokens, after_tokens, affected)` → 落 `EventType.compression`（`loop.py:288-307`）

**特殊路径：** compress_chain 整体异常 → 降级到 truncate 兜底（`loop.py:278-285`）

### 步骤 4：LLM 调用（~80 行）

**代码位置：** `loop.py:682-710` `_stream_from()` + `loop.py:331-376` 内层循环

**分流逻辑：**
- `config.transport.stream=True` → `backend.stream()`（流式 SSE chunks）
- `config.transport.stream=False` → `backend.complete()` → 手动构造 StreamEvent 序列

**完整链路：**
```
IR messages → encode_request() → API HTTP request → SSE chunks → decode_chunk() → StreamEvent
```

**DeepSeek codec 流式解码（`codecs/deepseek.py:247-379`）：**
- `DeepSeekStreamDecoder.decode_chunk()` 状态机
- thinking 边界推断（`loop.py:281-291`）：`reasoning_content` 出现→ThinkingStart，消失→ThinkingEnd
- tool_call index→id 映射：SSE 中先发 index 后发 id
- finish + usage 延迟发射（`_maybe_emit_end()` 等 usage chunk）

**Anthropic codec 流式解码（`codecs/anthropic.py:218-334`）：**
- `AnthropicStreamDecoder.decode_event()` 按 event type 分发
- `content_block_start/stop/delta` → ThinkingStart/ToolUseStart/TextDelta
- signature_delta → ThinkingEnd 时组装 signature
- 过滤 `SKIP_BLOCK_TYPES`（`codecs/anthropic.py:210-215`）

**_StreamAggregator（`loop.py:51-114`）：**
- 四种 buffer：`_text` / `_thinking` / `_tool_buffers` (dict[str, StringIO]) / `_tool_names`
- `result(message_end)` 组装逻辑：ThinkingBlock → TextBlock → ToolUseBlock（按 tool_order 保序）
- 空 blocks → 追加空 TextBlock 防挂

### 步骤 5：解析停止原因（~30 行）

**代码位置：** `loop.py:388-394`

**`StopReason` 分支表：**

| StopReason | 含义 | 下一步行为 | 对应厂商 finish_reason |
|------------|------|-----------|----------------------|
| `end_turn` | 正常结束 | `run_end(reason="end_turn")` | OpenAI `"stop"` / Anthropic `"end_turn"` |
| `max_tokens` | Token 用完 | `run_end(reason="max_tokens")` | OpenAI `"length"` / Anthropic `"max_tokens"` |
| `stop_sequence` | 命中停止序列 | `run_end(reason="stop_sequence")` | Anthropic `"stop_sequence"` |
| `tool_use` | 请求工具调用 | 进入工具执行阶段（步骤 6-7） | OpenAI `"tool_calls"` / Anthropic `"tool_use"` |
| `content_filter` | 内容被过滤 | `run_end(reason="content_filter")` | OpenAI `"content_filter"` / Anthropic `"refusal"` |
| `unknown` | 未知 | `run_end(reason="unknown")` | 正常路径不可达 |

**特殊处理：** `stop_sequence` 与 `end_turn` 同归为正常结束

### 步骤 6：权限检查（~60 行）

**代码位置：** `loop.py:498-541` `_check_tool_permission()` + `permission.py:135-160` `evaluate()`

**输入：** `ToolUseBlock` + `PermissionMode` + 用户规则列表
**输出：** `(decision: Decision, content: str, is_error: bool)`

**决策流程：**
```
1. rules 匹配（permission.py:140-146）
   → 如果 rule.pattern 匹配 operation → 返回 rule.action
   → DENY 最高优先级
2. 默认策略表（permission.py:103-132）
   → 根据 tool 分类查表
3. 返回 Decision.ALLOW / CONFIRM / DENY
```

**4 种模式的默认策略矩阵（`permission.py:103-132`）：**

| 操作类别 | safe | normal | autoedit | auto |
|---------|------|--------|----------|------|
| read（读文件/搜索） | ALLOW | ALLOW | ALLOW | ALLOW |
| write（写文件/patch） | DENY | CONFIRM | ALLOW | ALLOW |
| bash_safe（ls/echo/...） | DENY | CONFIRM | CONFIRM | ALLOW |
| bash_dangerous（rm/curl\|sh/...） | DENY | CONFIRM | CONFIRM | CONFIRM |

**危险命令分类（`permission.py:42-87`）：**
- 18 个安全命令正则（ls, git status/log/diff..., cat, head, tail, wc, echo, pwd, which, whoami, id, uname, env, date, printenv, type, cp, mv）
- 24 个危险命令正则（rm, rmdir, dd, chmod, chown, ln, kill, killall, pkill, reboot, shutdown, curl|sh, wget|sh, python -c, bash -c, sed -i, find -delete, fuser, mkfs, fdisk, exec, eval, >/dev/*）

**CONFIRM 分支（`loop.py:530-539`）：**
- 回调 `_on_permission(op, decision)` → TUI 弹窗交互（`app.py:142-162`）
- 无回调 → 默认 DENY（CLI 模式）

**审计日志：** 每次决策 emit `EventType.permission_check`（`loop.py:519-522`）

### 步骤 7：工具执行（~100 行）

**代码位置：** `loop.py:438-481`

**分流规则：**
- `concurrent_tools=True` 且 `tool_uses > 1` → `execute_concurrent()`
- 否则 → 串行执行

**并发路径（`loop.py:439-461`）：**

1. **ResourceScope 提取**（`concurrency.py:54-85` `_extract_scope()`）：

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

2. **冲突检测**（`concurrency.py:90-104` `_scopes_conflict()`）：
   - A 和 B 都是 READ → 不冲突
   - A 或 B 任一 WORKSPACE → 冲突
   - A.path == B.path → 冲突
   - A 为 PREFIX 且 B.path 在 A.path 下 → 冲突
   - 否则 → 不冲突

3. **分组调度**（`concurrency.py:118-138` `schedule()`）：
   - 贪心：每个 call 尝试加入第一个无冲突的现有组；无合适组则新建
   - 组内并发，组间串行

4. **并发执行**（`concurrency.py:146-212` `execute_concurrent()`）：
   - `ThreadPoolExecutor(max_workers=min(len(group), 4))`
   - 120 秒超时（`_CONCURRENT_TIMEOUT = 120.0`）
   - 失败传播：组 N 失败 → 组 N+1..M 跳过，返回 `[skipped: cancelled due to upstream failure]`

**串行路径（`loop.py:462-481`）：**
- 逐个 `handler = bound_tools.get(block.name)` → `handler(block.input)`
- 异常捕获：`PermissionError` / `FileNotFoundError` / `ValueError` → `ToolResultBlock(is_error=True)`

**公共逻辑：**
- 未知工具 → `"Unknown tool: {block.name}"`（`loop.py:466-469`）
- 单工具输出 50K 截断（`loop.py:558-565` `_truncate_tool_content()`）
- 更新 messages：`messages.append(resp.message)` + `messages.append(Message(role="user", content=tool_results))`（`loop.py:409,483`）

### 步骤 8：Checkpoint（~20 行）

**代码位置：** `loop.py:491-496` `_checkpoint()` + `loop.py:410,485` 调用点

**两处 persist 点：**
1. LLM 响应后（`loop.py:410`）：保存 LLM 输出结果
2. 工具执行后（`loop.py:485`）：保存本轮完整状态

**`Trajectory.persist()`（`trajectory.py:257-281`）：**
- WAL 模式 + 5 秒忙等超时
- 增量写入：只写 `_persisted_count` 之后的新事件
- `runs` 表 INSERT OR REPLACE（幂等）
- `events` 表 executemany

### 步骤 9：下一轮（~20 行）

**代码位置：** `loop.py:484` `turn_box[0] += 1`

**循环条件：** `while turn_box[0] < self.config.max_turns`（`loop.py:250`）

**终止条件汇总：**

| 触发点 | 代码位置 | 描述 |
|--------|---------|------|
| `end_turn` / `stop_sequence` | `loop.py:388-390` | 正常完成 |
| `max_tokens` / `content_filter` / `unknown` | `loop.py:392-394` | 异常完成 |
| `max_turns` 到达 | `loop.py:402-407` | 兜底（pending tool calls 被记录） |
| 重试耗尽 | `loop.py:348-355` | LLM 永久性失败 |
| 异常错误（工具绑定失败、并发执行异常等） | `loop.py:220-224, 444-447` | 紧急停止 |

### 步骤 10：完整调用图（ASCII art）（~80 行）

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

## 结尾

**下一篇推荐：** → 04-agent-runtime.md（专题展开 Agent 类结构）
**相关 ADR：** 0001（Agent 即库）、0006（Retry + Checkpoint）
**相关 plans：** 0002（agent-loop）、0003-agent-retry（重试系统）

---

## 本文件说明

这是文档 `03-a-single-turn-explained.md` 的细纲（大纲）。10 个步骤覆盖一次完整循环，每个步骤标注具体代码位置。写作时需确保 ASCII 图与最终代码完全一致。`core:loop.py:233` 等引用为实际行号的速查参考。

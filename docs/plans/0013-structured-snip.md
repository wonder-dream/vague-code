# 0013: 轨迹驱动的结构化压缩层（Trajectory-Driven Compression）

新增一层确定性压缩（插在 microcompact 和 auto_compact 之间），利用轨迹事件流中的结构化数据，零 LLM 成本生成任务摘要。

---

## 问题

当前压缩管线按精准度排序：

```
stale_snip（零成本、精准）→ microcompact（零成本、启发式）→ auto_compact（LLM 成本高）→ truncation（盲砍）
```

stale_snip 和 microcompact 都是**纯内容层面**的操作（看消息正文，不看语义）。auto_compact 是 LLM-based 语义摘要——但消融数据显示它对短会话（<30 turn）负收益（76% pass rate vs 83% 不开压缩）。

问题根源：`compress_chain` 的输入设计是 `messages → messages` 纯函数，完全不知道轨迹事件里已有的结构化信息——哪些文件被读了、哪些被改了、改了什么、测试跑没跑过——这些信息**在轨迹事件里已经全部结构化存储**，零推理成本，但压缩管线完全没用。

---

## 核心思路

在 microcompact 之后、auto_compact 之前，插一层 `structured_snip`：

```
stale_snip → microcompact → structured_snip → auto_compact → truncation
                               ↑ 新增
```

**输入**：`messages` + `events: list[Event]`

**输出**：将完成的"子任务"（读→改→测 闭环）替换为结构化摘要

**成本**：零 LLM 调用（纯规则匹配），只做事件流查询 + 消息替换

---

## 设计细节

### 1. 子任务边界识别算法

定义"闭合子任务"：从某个 turn 开始，经历 `read_file/grep/glob`（探索）→ `write_file/patch`（修改）→ `bash`（验证），到 `bash` 返回成功（`exit code 0` 且 `is_error=False`）为一个闭合周期。

```
算法：
1. 从后往前扫描 events，找最近 N 个闭合子任务（N = keep_recent_subtasks，默认 3）
2. 每个闭合子任务 = 从最后一个成功的 bash 反向追溯到最近的探索工具
3. 未闭合的归入"进行中子任务"，保留原文不压缩
```

### 2. 摘要生成模板

用事件 payload 中的结构化数据，模板生成摘要消息：

```
[Turn 3-5: Fixed bug in src/stats.py]
  read_file: src/stats.py
  grep: "def calculate" → found in src/stats.py, src/utils.py
  patch: src/stats.py L23 "pass" → "continue"
  bash: pytest tests/test_stats.py → exit 0 (all pass)
```

- `tool_call` 事件提供：工具名、参数（path/command/pattern）
- `tool_result` 事件提供：`is_error` 标志
- 消息替换时，将覆盖的原消息对标记为 `[compacted: see trajectory]`

### 3. 触发条件

```
new_total = count_tokens(...)
if new_total > budget * cfg.structured_snip_threshold:  # 默认 0.65
    messages, report = structured_snip(messages, events, ...)
```

放在 microcompact 和 auto_compact 之间。阈值取 0.65（高于 microcompact 的 0.5，低于 auto_compact 的 0.85），这样：
- 低负载时 microcompact 就够（不触发 structured_snip）
- 中负载时 structured_snip 触发（截住，不走到 auto_compact）
- 高负载时 auto_compact 再兜底

### 4. 事件作为输入源

关键问题：轨迹事件里 tool_call 的 payload 有完整的 `name` 和 `input` 字段。但压缩管线当前只接收 `messages`。

解法：
- `compress_chain` 签名扩展为 `compress_chain(messages, tools, cfg, budget, backend, model, skip_thinking, events=None)`
- `loop.py` 调用处传入 `traj.events`（`_run_gen` 方法内存中可用，无需查 SQLite）
- 向后兼容：`events=None` 时 structured_snip 直通

### 5. 安全约束

- 不破坏 tool_use/tool_result 配对（替换以整对为单位）
- 纯函数：输入 `(messages, events)` → 输出 `(messages, report)`，不写数据库
- 保留原文指针：被压缩的原文通过 `meta["compacted_by"] = "structured_snip"` + `meta["turn_range"]` 标记，可从 trajectory 恢复
- 摘要中不含 UUID/timestamp——只提取对 LLM 有用的语义信息

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `src/agent/config.py` | 改：`CompressionConfig` 加 `structured_snip_threshold: float = 0.65`、`structured_snip_keep_recent: int = 3` |
| 2 | `src/agent/context_compress.py` | **新建** `structured_snip()` 函数 + `_detect_subtasks()` 辅助函数 |
| 3 | `src/agent/context_compress.py` | 改：`compress_chain()` 签名 + `events` 参数，插在 microcompact 和 auto_compact 之间 |
| 4 | `src/agent/context.py` | 改：re-export `structured_snip`（可选，保持公共入口一致） |
| 5 | `src/agent/loop.py` | 改：`compress_chain()` 调用处传入 `traj.events` |
| 6 | `tests/test_structured_snip.py` | **新建**：子任务检测 + 摘要输出 + 配对不变量 + 无事件降级 |

---

## 预期收益

- **pass rate**：不开压缩 83%，开压缩 76%，预期 structured_snip 能将压缩 ON 的 pass rate 拉回接近 83%
- **token 消耗**：auto_compact 的 LLM 调用（~2-5K input + ~500 output）每触发一次可以被 structured_snip 省掉
- **延迟**：零额外 API 调用，事件流扫描 O(N)（N = tool_call 事件数），通常 < 50 个事件，可忽略

---

## 已知风险

| 风险 | 缓解 |
|------|------|
| 子任务边界判断不准（bash 后还有后续修改） | "从最后一个成功的 bash"回溯而非"第一个 bash"——宽松匹配，宁可少压不漏压 |
| 摘要丢失 LLM 编码的细粒度语义 | 保留原文 + trajectory 可恢复；且 structured_snip 替换的只是 messages 中的占位符，原文仍在 events 中 |
| 与 stale_snip/microcompact 的 meta 标记冲突 | 各层用不同 meta key 隔离（`stale` / `compacted` / `structured_snip`） |

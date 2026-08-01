# Context Engineering

**谁需要读：** 想理解 XClaw 如何管理上下文窗口的开发者
**前置阅读：** 05-tool-system.md（了解工具输出对上下文的压力）
**读完能做什么：** 理解五层压缩的完整机制、token 预算计算、可观测性设计

---

## 1. 概述

LLM 上下文窗口是稀有资源。在真实编码任务中，一轮对话通常产生 10K+ token（工具调用 + 工具结果），一个 SWE-bench 任务常超过 30 轮。不做压缩，任何模型都会在任务完成前溢出上下文窗口。

XClaw 使用**五层压缩流水线**解决这个问题：stale_snip（删被覆盖的旧读取）→ microcompact（折叠超长输出）→ structured_snip（轨迹驱动结构化压缩，零 LLM 成本）→ auto_compact（LLM 摘要）→ truncation（硬截断兜底）。五层按精准度排序，越靠前的越轻量、损失越小。

核心约束（ADR-0011）：
- **纯函数：** 压缩不写数据库、不改 trajectory 事件流
- **可观测：** 每层产出 `LayerReport`，记录 `EventType.compression`
- **可跳过：** `cfg.enabled=False` 完全关闭压缩
- **不破坏配对：** tool_use/tool_result 的配对关系在压缩后依然保持

---

## 2. 架构：3 个文件 + 1 个模块的职责

| 文件 | 职责 | 核心函数 / 类 |
|------|------|--------------|
| `context_compress.py` | 五层压缩实现 | `stale_snip()` / `microcompact()` / `structured_snip()` / `auto_compact()` / `truncate()` / `compress_chain()` |
| `context_tokens.py` | Token 计数 + 预算 | `count_tokens()` / `compute_budget()` / `should_skip_thinking()` / `per_message_tokens()` |
| `context_rules.py` | 规则文件层级加载 | `load_rules()` |
| `context.py` | SystemPrompt 构建 + 公共入口 | `SystemPrompt.build()` / `compress_chain()`（public re-export） |

**数据流：** `loop.py:263-319` → `context.py compress_chain()` → `context_compress.py compress_chain()` → 4 层内部调用 → `(messages, reports)` → loop.py

---

## 3. System Prompt 构造

`SystemPrompt.build()`（`context.py:9-30`）构造三段式 system prompt：

**Segment 1 — AGENT_IDENTITY**（`context.py:10-16`）：
硬编码行为守则，每次运行一致。内容包含：
- "你是 XClaw，一个编码智能体（Coding Agent）"
- "你的任务是阅读、理解、修改并测试代码"
- "修改文件之前必须先阅读它"
- "修改代码后运行测试验证正确性"
- "在编辑不熟悉的代码之前，使用 glob/grep 探索代码结构"
- "默认使用中文回答用户的所有问题"

**Segment 2 — 规则文件**（`context_rules.py:19-40` `load_rules()`）：
从项目根向当前目录反序遍历 parent 目录，最后加载 workdir 本级。安全上限：MAX_RULES_SIZE = 10KB / MAX_RULES_FILES = 20 / MAX_RULES_DEPTH = 50。UnicodeDecodeError 静默跳过。

**Segment 3 — 工作目录路径**（`context.py:29`）：
简短的 `工作目录根路径: {path}`。

**KV Cache 策略**（ADR-0007）：三段前缀固定，变动部分在末尾 → 最大化 cache hit。Segment 1 和 Segment 2 的大部分在前缀期不会变化，LLM provider 的 KV Cache 可以复用这些固定部分的计算结果。

---

## 4. 规则文件层级加载

`load_rules()` 的遍历策略（`context_rules.py:19-40`）：

```python
# 遍历所有祖先目录
for parent in reversed(root.parents):
    f = parent / ".agent/rules.md"
    if f.is_file() and f.stat().st_size <= MAX_RULES_SIZE:
        rules.append(f.read_text())
# 最后加载工作目录本身的规则
f = root / ".agent/rules.md"
if f.is_file() and f.stat().st_size <= MAX_RULES_SIZE:
    rules.append(f.read_text())
```

安全上限：

| 限制 | 值 | 原因 |
|------|-----|------|
| MAX_RULES_SIZE | 10KB | 防止超大规则文件占用 system prompt |
| MAX_RULES_FILES | 20 | 限制跨目录加载的数量 |
| MAX_RULES_DEPTH | 50 | 防止遍历过深 |

---

## 5. Token Budget 计算

**compute_budget()**（`context_tokens.py:131-136`）：

```python
def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    window = CONTEXT_WINDOWS.get(model, 64_000)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget
```

**CONTEXT_WINDOWS 表**（`context_tokens.py:14-19`）：

| 模型 | context_window | budget（×0.9） |
|------|---------------|----------------|
| deepseek-v4-flash | 1,000,000 | 900,000 |
| deepseek-v4-pro | 64,000 | 57,600 |
| claude-opus-4-8 | 200,000 | 180,000 |
| claude-sonnet-4-5 | 200,000 | 180,000 |

**双路径计数**（`context_tokens.py:47-55`）：
- `_get_enc()` → tiktoken `cl100k_base` 可用 → `_count_precise()` 精确计数
- tiktoken 不可用 → `_count_rough()` 字符 ÷4 粗略估算

**skip_thinking**（`context_tokens.py:24-30` `should_skip_thinking()`）：
- DeepSeek 链路 → True（codec 编码时丢弃 ThinkingBlock，token 计数也跳过）
- Anthropic 链路 → False（原生保留 thinking）
- 影响：compression 事件中的 `before_tokens`/`after_tokens` 明确标注 `skip_thinking` 值

**per_message_tokens()**（`context_tokens.py:119-128`）：单次 O(N) 预计算，返回 `list[int]`，供 truncate 的贪心循环使用，避免每次迭代全量复算。

---

## 6. 五层压缩流水线详解

### Layer 1：stale_snip

**代码位置：** `context_compress.py:69-136`

**触发条件：** 无条件执行（零 LLM 成本，纯规则）

**核心算法：**

1. `_find_pairs()` 扫描所有 assistant→user 消息对（`context_compress.py:36-46`）
2. 筛选 eligible pairs：`[-keep_recent:]` 的最新的 N 对豁免
3. 遍历每组对中 assistant 消息的 ToolUseBlock
4. 过滤出 read 类工具（`_READ_TOOLS = {"read", "read_file", "glob", "grep"}`）
5. 提取路径（`_extract_paths()`：从 `input["path"]` 或 `input["pattern"]`）
6. 按 `(tool_name, path)` 分组索引
7. 同组保留最后一条，前面的 content 替换为 `[stale: superseded by later {tool} of {path}]`

**不变量：**
- 不改消息结构，不删 tool_use_id
- `meta["stale"] = True` 标记 → microcompact 看到此标记不再重复折叠
- `meta["original_stale_content"]` 保留原文（可恢复）
- `is_error=True` 的结果跳过（错误结果不会被 stale 掉）

**边界情况：**
- 同一文件先 read 后 write → write 不是 read 工具，不触发 stale
- 不同工具读同一文件（read vs grep）→ 工具名不同，各自独立追踪
- `_extract_paths` 只追踪多路径 grep 的第一个路径

### Layer 2：microcompact

**代码位置：** `context_compress.py:151-208`

**触发条件：** `total > budget × microcompact_threshold`（默认 0.5）

**核心算法：**

1. `_find_pairs()` 扫描 eligible pairs（`[-keep_recent:]` 豁免）
2. 遍历每个 ToolResultBlock，检查 `content` 长度 > `max_chars=4000`
3. 豁免：已 stale（`block.meta.get("stale")`）或已 compacted（`"compacted" in block.meta`）
4. `_head_tail()` 取前 `_HEAD_LINES=20` + 后 `_TAIL_LINES=10` 行

**折叠格式：**

```
[compacted: {total} chars, {n} lines]
--- head (20 lines) ---
{head}
--- tail (10 lines) ---
{tail}
```

**字符级回退（PR-2 修复，`context_compress.py:179-186`）：**
- 条件：`head_n + tail_n >= total_lines`（head+tail 覆盖了全部内容）且 `len(content) > max_chars`
- 策略：前 50% + 后 50% 拼接，中间插入 `[{n} lines, {total} total chars]`

**原文指针：** `block.meta["compacted"] = {"original_chars": len(original), "tool_use_id": id}`

### Layer 3：structured_snip

**代码位置：** `context_compress.py:213-350`

**触发条件：** `total > budget × structured_snip_threshold`（默认 0.65）且 `events` 可用（非 None）

**设计动机：** stale_snip 和 microcompact 都是**纯内容层面**操作（看消息正文，不看语义）；auto_compact 是 LLM-based 摘要，消融数据显示它对短会话（<30 turn）负收益。根源是 `compress_chain` 完全不知道轨迹事件里已有的结构化信息——哪些文件被读了、被改了、测试跑没跑过。这些信息在轨迹事件里已全部结构化存储，零推理成本。structured_snip 利用这些信息，在 microcompact 之后、auto_compact 之前截住，避免走到 LLM 摘要。

**核心算法：**

1. `_detect_subtasks()` 从事件流识别"闭合子任务"：从最后一个成功的 bash（`is_error=False` 且内容含 `退出码: 0`）反向追溯，到最近的探索工具（read/grep/glob）为止
2. 未闭合的（进行中）子任务保留原文不压缩
3. 最近 `structured_snip_keep_recent=3` 个子任务豁免
4. 用事件 payload 中的结构化数据模板生成摘要消息，整对替换

**摘要模板**（从事件 payload 提取，不含 UUID/timestamp）：

```
[已完成子任务 (turn 0-2)]
  read_file: stats.py
  patch: stats.py
  bash: pytest tests/test_stats.py
```

**不变量：**
- **纯函数：** 输入 `(messages, events)` → 输出 `(messages, report)`，不写数据库
- **不破坏配对：** 替换以整对（assistant+user）为单位
- **保留原文指针：** `meta["compacted_by"]="structured_snip"` + `meta["turn_range"]`，原文在 trajectory 事件流中可恢复
- **向后兼容：** `events=None` 时该层直通，不改变既有调用方

**零 LLM 成本：** 纯规则匹配，只做事件流查询 + 消息替换。事件流扫描 O(N)（N = tool_call 事件数），通常 < 50 个事件，延迟可忽略。

**与 memory 协同：** 无（structured_snip 不做蒸馏；蒸馏仍由 auto_compact 承担，见 08-memory-system.md）。

### Layer 4：auto_compact

**代码位置：** `context_compress.py:352-490`

**触发条件：**
- `total > budget × auto_compact_threshold`（默认 > 0.85）
- stale_snip + microcompact + structured_snip 后仍高于阈值
- backend 可用（非 None）

**核心算法：**

1. 确定摘要范围：system + 所有 pair 中最近 `keep_turns=4` 对以前的部分
2. 序列化历史为 LLM 可消费的摘要请求（`context_compress.py:267-293`）：
   - `_SUMMARIZE_PROMPT` + "\n---\n" + 角色/内容序列化
   - ToolUseBlock → `[tool: name({input})]`
   - ToolResultBlock → `[result: {content_truncated_200chars}]`
   - ThinkingBlock → `[thinking: {text_truncated_200chars}]`
3. 调用 `backend.complete(messages=[summary_msg], tools=None, config={"model": model, "stream": False})`
4. 重建消息：
   ```
   [system(保留), Message("user", f"[Session summary]\n{summary}"), ...最近 keep_turns 轮原文]
   ```

**失败降级：** 任何异常 → 跳过 auto_compact（`context_compress.py:302-310`），交给 truncate 兜底。

**摘要蒸馏：** auto_compact 摘要 → `memory_store.ingest()` 入库（`loop.py:323-329`），实现跨会话记忆的自动写入。

**LayerReport 记录：** summary_input_tokens / summary_output_tokens / original_messages / summary_text

### Layer 5：truncation

**代码位置：** `context_compress.py:492-620`

**触发条件：** 前三层压缩后仍 > budget

**核心算法：**

1. 锁定 prefix：system（如果有）+ 首条 user（任务目标）
2. 预计算 per-message token（`per_message_tokens()`，`context_compress.py:388-389`）
3. 从尾部贪心回填：反转 `_find_pairs()` 结果，取最近的 pair
4. 每次加 assistant+user 原子对（确保 tool_use/tool_result 配对）
5. 独占消息（无配对的 user）也尝试加入
6. 丢弃处插入 truncation marker（`context_compress.py:454-459`）：
   - `[truncated: dropped {n} messages to fit token budget]`
   - 如果 marker + tail 超 budget → 从 tail 弹出最旧 pair 腾空间
7. 返回 `reconstructed = prefix + marker + tail`

**边界情况：**
- prefix 本身超 budget → 返回 prefix only（best effort，`context_compress.py:394-401`）
- 标记回填循环优化（R2 修复）：per-message cache 降 O(P×C) → O(C)，`context_compress.py:388-389`

---

## 7. 压缩流水线编排

**compress_chain()**（`context_compress.py:489-526`）：

```python
def compress_chain(messages, tools, cfg, budget, backend=None, model="", skip_thinking=True, events=None):
    if not cfg.enabled:
        return messages, []

    reports = []

    # Layer 1：无条件执行
    messages, report = stale_snip(messages, cfg.stale_snip_keep_recent, ...)
    reports.append(report)

    # Layer 2：利用率 > microcompact_threshold
    new_total = count_tokens(messages, ...)
    if new_total > budget * cfg.microcompact_threshold:
        messages, report = microcompact(messages, ...)
        reports.append(report)
        new_total = count_tokens(messages, ...)

    # Layer 3：利用率 > structured_snip_threshold AND events 可用（零 LLM 成本）
    if events is not None and new_total > budget * cfg.structured_snip_threshold:
        messages, report = structured_snip(messages, events, cfg.structured_snip_keep_recent, ...)
        reports.append(report)
        new_total = count_tokens(messages, ...)

    # Layer 4：利用率 > auto_compact_threshold AND backend 可用
    if backend is not None and new_total > budget * cfg.auto_compact_threshold:
        messages, report = auto_compact(messages, backend, model, ...)
        reports.append(report)
        new_total = count_tokens(messages, ...)

    # Layer 5：仍超 budget
    if new_total > budget:
        messages, report = truncate(messages, budget, ...)
        reports.append(report)

    return messages, reports
```

每层执行后检查是否仍然超 budget，只有仍超时才进入下一层。这种"见好就收"的设计避免了不必要的压缩开销。

---

## 8. 消融数据讨论

消融实验固定其他变量，开关压缩和并发两个特性，在 30 个 SWE-bench Lite 任务上测试：

| Compression | Concurrency | Pass Rate | Avg Tokens |
|------------|-------------|-----------|------------|
| ✗ | ✗ | 83% | 635K |
| ✗ | ✓ | 93% | 614K |
| ✓ | ✗ | 76% | 735K |
| ✓ | ✓ | 73% | 759K |

与基线（压缩 OFF + 并发 OFF）的对比：

| 配置 | Pass Rate 变化 | Tokens 变化 |
|------|---------------|-------------|
| Compression OFF + Concurrency OFF | 基准（83%） | 基准（635K） |
| Compression OFF + Concurrency ON | **+10pp** | **-3%** |
| Compression ON + Concurrency OFF | -7pp | +16% |
| Compression ON + Concurrency ON | -10pp | +20% |

**关键发现：**

1. **并发是最大单项增益（+10pp pass rate），同时 token 消耗最低**——多工具并行不增加语义轮次
2. **压缩在中小任务（<30 turns）中负收益**——auto_compact 的 LLM 调用成本高于回收的 token 空间
3. **压缩 + 并发存在负协同**——auto_compact 的摘要请求与主 Agent LLM 调用共享 backend，产生资源竞争
4. **structured_snip 的定位**：在中间层截住，用零 LLM 成本的结构化摘要替代 auto_compact 的 LLM 调用，预期将压缩 ON 的 pass rate 拉回接近基线（83%）——具体数值待真实 API 消融重跑验证
5. 压缩设计目标为 30+ 轮长会话——在短任务中可以通过 `cfg.enabled=False` 关闭

从轨迹中量化各层效果：每层 emit `EventType.compression`（before_tokens, after_tokens），从 SQLite 聚合可以计算各层的 token 回收率。

---

## 下一篇

→ **07-permission-system.md**：权限安全体系——四级安全模式和三层规则系统。

**相关 ADR：** 0007（System Prompt）、0008（Rules）、0009（Token Budget）、0010（Module Architecture）、0011（Compression Pipeline）、0017（structured_snip）
**相关 plans：** 0007（system-prompt-layer-injection）、0008（compression-pipeline）、0013（structured-snip）
**相关 blog：** compression.md（压缩设计博客文章）

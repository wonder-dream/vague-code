# 细纲：06-context-engineering.md

**预估行数：** ~600 行
**定位：** 上下文压缩与 token 管理的完整设计。

---

## 开头

- **谁需要读：** 想理解 XClaw 如何管理上下文窗口的开发者
- **前置阅读：** 05-tool-system.md（了解工具输出对上下文的压力）
- **读完能做什么：** 理解五层压缩的完整机制、token 预算计算、可观测性设计

---

## 细纲

### 1. 概述（~30 行）

- 难题陈述：LLM 上下文窗口是稀有资源。SWE-bench 任务常超 30 轮，每轮 tool call 产 10K+ token，不压缩必然溢出
- 五层压缩流水线（stale_snip → microcompact → auto_compact → truncation），按精准度排序回收 token
- 核心约束（ADR-0011）：纯函数、可观测、可跳过、不破坏 tool_use/tool_result 配对

### 2. 架构：3 个文件 + 1 个模块的职责（~40 行）

| 文件 | 职责 | 核心函数 / 类 | 作用 |
|------|------|--------------|------|
| `context_compress.py` | 五层压缩实现 | `stale_snip()` / `microcompact()` / `auto_compact()` / `truncate()` / `compress_chain()` | — |
| `context_tokens.py` | Token 计数 + 预算 | `count_tokens()` / `compute_budget()` / `should_skip_thinking()` / `per_message_tokens()` | — |
| `context_rules.py` | 规则文件层级加载 | `load_rules()` | — |
| `context.py` | SystemPrompt 构建 + 公共入口 | `SystemPrompt.build()` / `compress_chain()`（public re-export） | — |

**数据流：** `loop.py:263-319` → `context.py compress_chain()` → `context_compress.py compress_chain()` → 4 层内部调用 → `(messages, reports)` → `loop.py`

### 3. System Prompt 构造（~40 行）

**`SystemPrompt.build()`（`context.py:9-30`）：**

三段式结构：
1. **AGENT_IDENTITY**（`context.py:10-16`）：硬编码行为守则
   - "Task: read, understand, modify, and test code"
   - "Always read a file before editing it"
   - "Run tests after making changes"
   - "Use glob/grep to explore unfamiliar codebases"
2. **规则文件**（`context_rules.py:19-40` `load_rules()`）
   - 从根向当前目录反序遍历 parent 目录→最后加载 workdir 本级
   - 安全上限：MAX_RULES_SIZE = 10KB / MAX_RULES_FILES = 20 / MAX_RULES_DEPTH = 50
   - UnicodeDecodeError 静默跳过
3. **工作目录路径**（`context.py:29`）

**KV Cache 策略：** 三段前缀固定，变动部分在末尾 → 最大化 cache hit（ADR-0007）

**ADR 引用：** 0007（System Prompt 分层注入架构）

### 4. 规则文件层级加载（~40 行）

**`load_rules()` 遍历策略（`context_rules.py:19-40`）：**
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

**安全上限汇总：**
| 限制 | 值 | 原因 |
|------|-----|------|
| MAX_RULES_SIZE | 10KB | 防止超大规则文件占用 system prompt |
| MAX_RULES_FILES | 20 | 限制跨目录加载的数量 |
| MAX_RULES_DEPTH | 50 | 防止遍历过深 |

### 5. Token Budget 计算（~40 行）

**`compute_budget()`（`context_tokens.py:131-136`）：**
```python
def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    window = CONTEXT_WINDOWS.get(model, 64_000)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget
```

**`CONTEXT_WINDOWS` 表（`context_tokens.py:14-19`）：**

| 模型 | context_window | budget（×0.9） |
|------|---------------|----------------|
| deepseek-v4-flash | 1,000,000 | 900,000 |
| deepseek-v4-pro | 64,000 | 57,600 |
| claude-opus-4-8 | 200,000 | 180,000 |
| claude-sonnet-4-5 | 200,000 | 180,000 |

**双路径计费（`context_tokens.py:47-55`）：**
- `_get_enc()` → tiktoken `cl100k_base` 可用 → `_count_precise()`（精确）
- tiktoken 不可用 → `_count_rough()`（字符 ÷4 粗略估算）

**`skip_thinking` 参数（`context_tokens.py:24-30 ` `should_skip_thinking()`）：**
- DeepSeek 链路 → `True`（codec 编码时丢弃 ThinkingBlock）
- Anthropic 链路 → `False`（原生保留 thinking）
- 影响：compression 事件中的 `before_tokens`/`after_tokens` 明确标注 `skip_thinking` 值

**`per_message_tokens()`（`context_tokens.py:119-128`）：**
- 单次 O(N) 预计算，返回 `list[int]`（truncate 贪心循环使用）
- 用途：避免 truncate 每次迭代全量复算

**ADR 引用：** 0009（Token Budget）、0010（Context Module Architecture）

### 6. 五层压缩流水线详解（~250 行）

#### Layer 1：stale_snip（~60 行）

**代码位置：** `context_compress.py:69-136`

**触发条件：** 无条件执行（零 LLM 成本）
**核心算法：**
1. `_find_pairs()` 扫描所有 assistant→user 消息对（`context_compress.py:36-46`）
2. 筛选 eligible pairs：`[-keep_recent:]` 的最新的 N 对豁免
3. 遍历每组对中 assistant 消息的 ToolUseBlock
4. 过滤出 read 类工具（`_READ_TOOLS = {"read", "read_file", "glob", "grep"}`）
5. 提取路径（`_extract_paths()`：`input["path"]` / `input["pattern"]`）
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
- `_extract_paths` 只追踪多路径 grep 的第一个路径（C4 修复后）

#### Layer 2：microcompact（~60 行）

**代码位置：** `context_compress.py:151-208`

**触发条件：** `total > budget × microcompact_threshold`（`cfg.microcompact_threshold` 默认 0.5）
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
- 条件：`head_n + tail_n >= total_lines`（head+tail 覆盖全内容）且 `len(content) > max_chars`
- 策略：前 50% + 后 50% 拼接，中间插入 `[{n} lines, {total} total chars]`

**原文指针：** `block.meta["compacted"] = {"original_chars": len(original), "tool_use_id": id}`

#### Layer 3：structured_snip（~40 行）

**代码位置：** `context_compress.py:213-350`

**触发条件：** `total > budget × structured_snip_threshold`（默认 0.65）且 `events` 可用（非 None）

**设计动机：**
- stale_snip/microcompact 是纯内容层面操作；auto_compact 是 LLM 摘要，短会话负收益
- 轨迹事件里已有结构化信息（读了哪些文件、改了哪些、测试跑没跑）——零推理成本，但原管线完全没用
- structured_snip 在中间截住，避免走到 LLM 摘要

**核心算法：**
1. `_detect_subtasks()`：从最后成功的 bash（`is_error=False` + `退出码: 0`）反向追溯，到最近探索工具（read/grep/glob）
2. 未闭合子任务保留原文不压缩
3. 最近 `structured_snip_keep_recent=3` 个子任务豁免
4. 用事件 payload 模板生成摘要消息，整对替换

**摘要模板：**
```
[已完成子任务 (turn 0-2)]
  read_file: stats.py
  patch: stats.py
  bash: pytest tests/test_stats.py
```

**不变量：**
- 纯函数：输入 `(messages, events)` → 输出 `(messages, report)`
- 整对替换，不破坏 tool_use/tool_result 配对
- `meta["compacted_by"]="structured_snip"` + `meta["turn_range"]` 保留原文指针
- 向后兼容：`events=None` 直通

**零 LLM 成本：** 纯规则匹配，事件流扫描 O(N)（N = tool_call 事件数，通常 < 50）

#### Layer 4：auto_compact（~70 行）

**代码位置：** `context_compress.py:352-490`

**触发条件：**
- `total > budget × auto_compact_threshold`（默认 > 0.85）
- stale_snip + microcompact + structured_snip 后仍高于阈值
- backend 可用（非 None）

**核心算法：**
1. 确定摘要范围：system + 所有 pair 中最近 `keep_turns=4` 对以前的部分
2. 序列化历史→LLM 摘要请求（`context_compress.py:267-293`）：
   - `_SUMMARIZE_PROMPT` + "\n---\n" + 角色/内容序列化
   - ToolUseBlock → `[tool: name({input})]`
   - ToolResultBlock → `[result: {content_truncated_200chars}]`
   - ThinkingBlock → `[thinking: {text_truncated_200chars}]`
3. 调用 `backend.complete(messages=[summary_msg], tools=None, config={"model": model, "stream": False})`
4. 重建消息（`context_compress.py:330-334`）：
   ```
   [system(保留), Message("user", f"[Session summary]\n{summary}"), ...最近 keep_turns 轮原文]
   ```

**失败降级：** 任何异常 → 跳过 auto_compact（`context_compress.py:302-310`），交给 truncate 兜底
**摘要蒸馏：** auto_compact 摘要 → `memory_store.ingest()` 入库（`loop.py:323-329`）
**LayerReport 记录：** summary_input_tokens / summary_output_tokens / original_messages / summary_text

#### Layer 5：truncation（~60 行）

**代码位置：** `context_compress.py:492-620`

**触发条件：** 前四层压缩后仍 `>` budget
**核心算法：**
1. 锁定 prefix：system（如果有）+ 首条 user（任务目标）
2. 预计算 per-message token（`per_message_tokens()`，`context_compress.py:388-389`）
3. 从尾部贪心回填：反转 `_find_pairs()` 结果，取最近的 pair
4. 每次加 assistant+user 原子对（确保 tool_use/tool_result 配对）
5. 独占消息（无配对的 user）也尝试加入
6. 丢弃处插入 truncation marker（`context_compress.py:454-459`）：
   - 尝试插入 `[truncated: dropped {n} messages to fit token budget]`
   - 如果 marker + tail 超 budget → 从 tail 弹出最旧 pair 腾空间
7. 返回 `reconstructed = prefix + marker + tail`

**边界情况：**
- prefix 本身超 budget → 返回 prefix only（best effort，`context_compress.py:394-401`）
- 标记回填循环优化（R2 修复）：per-message cache 降 O(P×C) → O(C)，`context_compress.py:388-389`

### 7. 压缩流水线编排（~40 行）

**`compress_chain()`（`context_compress.py:625-680`）：**
```python
def compress_chain(messages, tools, cfg, budget, backend=None, model="", skip_thinking=True, events=None):
    if not cfg.enabled:
        return messages, []

    reports = []

    # Layer 1：无条件
    messages, report = stale_snip(messages, cfg.stale_snip_keep_recent, ...)
    reports.append(report)

    # Layer 2：util > microcompact_threshold
    new_total = count_tokens(messages, ...)
    if new_total > budget * cfg.microcompact_threshold:
        messages, report = microcompact(messages, ...)
        reports.append(report)
        new_total = count_tokens(messages, ...)

    # Layer 3：util > structured_snip_threshold AND events（零 LLM 成本）
    if events is not None and new_total > budget * cfg.structured_snip_threshold:
        messages, report = structured_snip(messages, events, cfg.structured_snip_keep_recent, ...)
        reports.append(report)
        new_total = count_tokens(messages, ...)

    # Layer 4：util > auto_compact_threshold AND backend
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

### 8. 消融数据讨论（~40 行）

来源：`README.md:123-132`、`eval/results.md:3-8`

| Compression | Concurrency | Pass Rate | Avg Tokens |
|------------|-------------|-----------|------------|
| ✗ | ✗ | 83% | 635K |
| ✗ | ✓ | **93%** | **614K** |
| ✓ | ✗ | 76% | 735K |
| ✓ | ✓ | 73% | 759K |

| 配置 | 对比基线（60% / 931K） |
|------|----------------------|
| Compression OFF + Concurrency OFF | +23pp pass rate, -32% tokens |
| Compression OFF + Concurrency ON | **+33pp pass rate, -34% tokens（最佳）** |
| Compression ON + Concurrency OFF | +16pp pass rate, -21% tokens |
| Compression ON + Concurrency ON | +13pp pass rate, -18% tokens |

**关键发现：**
1. **并发提升最大（93% pass rate），同时 token 消耗最低**——多工具并行不增加语义轮次
2. **压缩在小任务（<30 turns）中负收益**——auto_compact 的 LLM 调用成本高于回收收益
3. **压缩 + 并发存在负协同**——auto_compact 摘要请求与主 Agent LLM 调用共享 backend 产生竞争
4. 压缩设计目标为 30+ 轮长会话

**如何从轨迹量化：** 每层 emit `EventType.compression`（before_tokens, after_tokens）→ 从 SQLite 聚合可算各层回收率

---

## 结尾

**下一篇推荐：** → 07-permission-system.md（权限安全体系）
**相关 ADR：** 0007（System Prompt）、0008（Rules）、0009（Token Budget）、0010（Module Architecture）、0011（Compression Pipeline）
**相关 plans：** 0007（system-prompt-layer-injection）、0008（compression-pipeline）
**相关 blog：** compression.md（压缩设计博客文章）

---

## 本文件说明

这是文档 `06-context-engineering.md` 的细纲（大纲）。实际写作时需确保每层算法步骤与实际 `context_compress.py` 逐行对应。消融数据部分需引用 README.md 和 eval/results.md 中的最新数据。

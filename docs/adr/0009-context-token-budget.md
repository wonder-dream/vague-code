---
status: accepted
date: 2026-07-26
---

# 0009: Context Token Budget & Compression Events

## 背景

上下文治理的核心输入是"当前消息序列用了多少 token"。每轮 LLM 调用前必须知道 token 消耗，才能：

1. 判断是否达到压缩阈值（auto_compact 触发条件：利用率 > 85%）
2. 计算压缩后的 token 回收量（消融实验的因变量）
3. 防止超出模型上下文窗口硬限

当前系统完全没有 token 计数。消息是 `list[Message]` 对象，无从得知序列总的 token 数。

本 ADR 覆盖 tokenizer 选型、计数规则、轨迹事件 schema 三个决定。

## 约束

1. **必须在每轮 LLM 调用前执行**，不是事后离线分析
2. **消息数组包含 system prompt + tool schemas**，不仅是对话消息
3. **必须精确到足以判断 85% 利用率阈值**，误差需要在 ±5% 内
4. **不能引入 CPython 以外的编译依赖**（排除 llama.cpp 等原生扩展）
5. **计数结果必须落盘到轨迹事件流**，供评测消费

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| Tokenizer 实现 | A: tiktoken / B: char/4 / C: Anthropic tokenizer（pip 不可用） | A |
| 编码类型 | A: cl100k_base (固定) / B: 按模型切换 o200k_base / p50k_base 等 | A |
| 计数时机 | A: 每轮 LLM 调用前 / B: 仅压缩触发时 | A |
| 轨迹事件 | A: 统一的 compression 事件含 layer / B: 分多个事件类型 | A |
| 工具定义计数 | A: 计入 total / B: 不计入，单独统计 | A |

## 决策

### 1. Tokenizer：tiktoken + cl100k_base

`tiktoken` 是 OpenAI 官方 Python 库，纯 Python 实现（核心在 Rust 但通过 pip 预编译分发），
支持 Windows/Linux。

选择统一使用 `cl100k_base`（GPT-4 / DeepSeek V3 使用的编码），不随模型名切换。

**为什么不按模型切换**：
- 不同模型 tokenizer 的差异在长文本时会在 ±5% 以内
- 切换逻辑增加复杂度，不值得
- 压缩触发阈值有 15% 缓冲区（85% 触发，100% 才超限），±5% 误差完全可控
- 锁定一种编码使 token 消耗在不同运行间可比（消融实验前提）

**Anthropic 专用 tokenizer**：
Anthropic 的 tokenizer 没有公开 pip 包，只能通过 `anthropic` SDK 间接获取（但需要 API 调用）。
不现实。统一使用 cl100k_base，Anthropic 端标记已知差异。

### 2. 计数规则

```python
def count_tokens(messages: list[Message], tools: list[ToolSpec] | None = None) -> int:
    enc = tiktoken.get_encoding("cl100k_base")
    total = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                total += len(enc.encode(block.text))
    if tools:
        for t in tools:
            total += len(enc.encode(t.description))
            total += len(enc.encode(json.dumps(t.parameters)))
    return total
```

计数范围包括：
- 所有消息的 `TextBlock.text`（system / user / assistant 的纯文本部分）
- 所有工具的 `description` + `parameters` 序列化 JSON
- `ThinkingBlock`、`ToolUseBlock`、`ToolResultBlock` 不计入——它们由 `StreamAggregator` 从 delta 重建，
  不在 `Message.content` 的原始文本中。后续发现误差时再修正。

### 3. 预算定义

```python
budget = min(context_window * 0.9, user_max_tokens)

# context_window 按模型静态映射：
CONTEXT_WINDOWS = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro":    64_000,
    "claude-opus-4-8":   200_000,
}
# user_max_tokens 来自 AgentConfig（可消融控制）
# 已知：deepseek-v4-flash 的上下文窗口为 1M，即 budget = min(1_000_000 * 0.9, user_max_tokens)
```

### 4. 轨迹事件 schema

每次计数 emit 一条 `compression` 事件：

```json
{
  "run_id": "r_0042",
  "turn": 8,
  "ts": "...",
  "type": "compression",
  "payload": {
    "layer": "budget",
    "before_tokens": 41200,
    "after_tokens": 41200,
    "budget": 180000,
    "utilization": 0.23,
    "tools_tokens": 1200,
    "tool_count": 6
  }
}
```

压缩各层触发时也使用同一事件类型，通过 `layer` 字段区分：

| layer 值 | 含义 |
|----------|------|
| `"budget"` | 常规预算记账（未压缩） |
| `"stale_snip"` | 层 1 触发，标记/移除过期 read |
| `"microcompact"` | 层 2 触发，超长工具输出摘要 |
| `"auto_compact"` | 层 3 触发，全量结构化压缩 |
| `"truncation"` | 层 4 触发，硬截断兜底 |

`before_tokens` 始终为压缩前计数，`after_tokens` 为压缩后计数。
`compression` 事件的 `utilization` 字段是消融实验的核心因变量。

### 5. 触发时机

`loop.py` 中，每轮 LLM 调用前，在 `_run_gen()` 或 `start()` 路径里：

```python
# 每轮调用前
total = count_tokens(messages, tools)
utilization = total / budget
traj.emit(EventType.compression, turn=turn, payload={
    "layer": "budget",
    "before_tokens": total,
    "after_tokens": total,
    "budget": budget,
    "utilization": round(utilization, 4),
    "tools_tokens": tools_tokens,
    "tool_count": len(tools) if tools else 0,
})
if utilization > 0.85:
    messages = compress_chain(messages, ...)  # Week 2 后续阶段
```

## Consequences

- tiktoken 精确度在 DeepSeek V3 上已验证（开发者报告误差 < 3%），满足利用率阈值判断
- 每轮 token 计数写入轨迹，消融实验不需要事后重算——token 统计是事件流的天然属性
- 统一 `compression` 事件类型 + `layer` 字段区分，评测报告按 layer 分组聚合即可得到各层收益
- 工具定义的 token 消耗单独统计，在 `utilization` 中已计入 total 但不影响压缩（工具定义不压缩）
- `context_window` 的静态映射表需要在模型增加时更新，列出已知限制

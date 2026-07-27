---
status: accepted
date: 2026-07-27
---

# 0011: 四层压缩流水线

## 背景

Week 2 上下文治理的核心——四层压缩流水线（stale_snip → microcompact → auto_compact → truncation），在 LLM 上下文利用率超过阈值时按精准度降序回收 token。

ADR-0009 预留了 `compress_chain()` 入口位置，ADR-0010 规划了 `context_compress.py` 文件。本 ADR 做四层的设计决策。

## 约束

1. **纯函数**——压缩 `messages → messages`，不写数据库、不改 trajectory 事件流。跨轮不持久化，resume 重建完整历史后下轮自然重新压缩
2. **可观测**——每层产出 `LayerReport` 落 `EventType.compression` 事件，支持离线重算和 M2 量化
3. **可跳过**——每层可独立关闭；auto_compact 在无 backend 注入时静默跳过
4. **不引入新依赖**——压缩用 `count_tokens` 做计量，不额外 import tiktoken 以外的 tokenizer；microcompact/truncation 纯规则，不调 LLM
5. **不破坏 tool_use/tool_result 配对**——任何层都不删除消息，只替换 block content

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| 触发策略 | A: 每轮全链执行 / B: 按 utilization 阈值门控 | **B** |
| auto_compact 摘要模型 | A: 同一 backend 非流式 complete / B: 独立摘要模型 | **A** |
| microcompact 折叠方法 | A: 规则 head+tail / B: LLM 摘要 | **A** |
| ThinkingBlock token 处理 | A: 计入花销 / B: 跳过 | **B（默认跳过）** |
| 压缩结果持久化 | A: 写入 trajectory 事件流 / B: 仅内存 | **B** |
| stale_snip 识别依据 | A: file path 精确匹配 / B: 模糊路径匹配 | **A** |

## 架构

### 文件职责

```
context.py:                  # 已有
  - SystemPrompt.build()
  - compress_chain()         # 新增入口，路由到 context_compress

context_compress.py:         # 新增
  - stale_snip()
  - microcompact()
  - auto_compact()
  - truncate()
  - compress_chain()         # 实现层面的串联

context_tokens.py:           # 已有，略改
  - count_tokens()           # 加 skip_thinking 参数
  - compute_budget()
```

### 触发门控

```
每轮 LLM 调用前：
  total = count_tokens(messages, tools, skip_thinking=…)
  if total < budget * microcompact_threshold (0.5):
     直通（只做 stale_snip 零成本扫描）
  elif total < budget * auto_compact_threshold (0.85):
     执行 → stale_snip + microcompact
  else:
     执行 → stale_snip + microcompact + auto_compact
  if total > budget:
     执行 → truncation（兜底）
```

stale_snip 本身零成本且不丢信息，无条件执行。不在压缩立即执行的轮次里也跑 stale_snip。

## 四层设计

### 层 1：stale_snip

**输入 `messages` → 扫描所有 ToolResultBlock 关联 ToolUseBlock，被后序同路径覆盖的替换为占位符**

- 按时间顺序扫描，对 `name in ("read", "glob", "grep")` 的工具结果，取 `input.path` 做精确匹配
- 同一 path 被多次操作 → 除最后一条外，前面的 ToolResultBlock.content 替换为 `"[stale: superseded by later read of {path}]"`
- 最近 `stale_snip_keep_recent=3` 条工具结果豁免（保护刚产生的数据不被立即 snip）
- `is_error=True` 的结果跳过
- `meta["stale"] = True` 标记，microcompact 看到此标记不再重复折叠
- **不改消息结构**，不删 tool_use_id，不走"stale"标记——content 替换后配对依然完整
- 轨迹中原文通过事件流恢复

### 层 2：microcompact

**输入 `messages` → 对超长 ToolResultBlock 做 head+tail 结构化折叠，保留原文指针**

- 触发条件：content 长度 > `microcompact_max_chars=4000`
- 豁免：最近 `microcompact_keep_recent=3` 条、已 stale 的
- 折叠格式：
  ```
  [compacted: {total} chars, {n} lines, tool={name}]
  --- head (20 lines) ---
  {head}
  --- tail (10 lines) ---
  {tail}
  ```
- `meta["compacted"] = {"original_chars": len(original), "tool_use_id": id}`——保留原文指针，模型可通过 read 再拿全量
- head/tail 行数不可配，写死以缩减决策树；若需要调整，后续加 `CompressionConfig.microcompact_head_lines`

### 层 3：auto_compact

**输入 `messages` → 调 backend.complete() 对历史做摘要，保留最近 N 轮原文**

- 触发条件：utilization > 0.85 且 stale_snip + microcompact 后仍 > budget（即前两层回收不够）
- 入参：`(messages, backend, model, keep_turns)`
- 构造摘要请求：`Message("user", SUMMARIZE_PROMPT + "\n---\n" + 序列化历史)`
- 摘要 prompt：
  ```
  You are a summarization engine. Summarize the coding session below.
  Include: what the user's task is, what has been done so far,
  key file paths and changes, pending work, and any errors or blocking issues.
  The summary will be used to continue the session.
  ```
- 重建后结构：`[system(保留), Message("user", f"[Session summary]\n{summary}"), ...最近 keep_turns 轮原文]`
- 摘要请求本身调用 `backend.complete(messages=summary_messages, tools=None, config={"model": model, "stream": False})`
- 摘要失败 → 降级到 truncation 兜底，不影响主循环
- 摘要轮消耗的 token 计入 auto_compact 的 LayerReport

### 层 4：truncate

**输入 `messages` → 从尾部贪心回填，丢弃中间部分直到低于 budget**

- 保留：system message + 首条 user（任务目标，不做配对比检查）
- 从尾往前贪心回填：每次取出消息对（assistant + 其后的 user/tool_results）
  - 如果当前出的是 assistant → 连带其后的 user 下条一起取
  - 如果当前出的是 user → 单独取（纯用户消息，通常是摘要或外发问题）
- 配对齐一性：tool_use/tool_result 作为 assistant/user 对自然同步移除
- 丢弃处插标记 `Message("user", content=[TextBlock("[truncated: dropped {n} messages to fit budget])"])`
- `count_tokens` 预算检查，精确到不超过 budget

## ThinkingBlock 的 token 记账

现有实现中 `count_tokens` 将 ThinkingBlock 计入 token 估算。

**决策**：`count_tokens` 加参数 `skip_thinking: bool = False`。DeepSeek 链路传 `True`（codec 编码时丢弃 thinking），Anthropic 链路传 `False`（同轮 tool_use 续接需保留 thinking）。

影响点：
- `compress_chain` 内 budget 检查使用 `skip_thinking=True`
- `loop.py:231` 的利用率计算也使用 `skip_thinking=True`（高估会触发过早压缩，低估不会——低估比高估安全）
- 压缩事件的 `before_tokens`/`after_tokens` 明确标注 `skip_thinking=True` 及其原始值

## CompresssionConfig 扩展

```python
@dataclass
class CompressionConfig:
    enabled: bool = True
    microcompact_threshold: float = 0.5
    microcompact_max_chars: int = 4000
    microcompact_keep_recent: int = 3
    auto_compact_threshold: float = 0.85
    auto_compact_keep_turns: int = 4
    stale_snip_keep_recent: int = 3
```

### 与已有 50_000 硬截断的关系

`loop.py:324` 的 `MAX_TOOL_CONTENT = 50_000` 是**单条工具输出**的最后一道保险，执行时截断。microcompact 是**历史消息**的事后治理。职责不冲突，50_000 继续保持。

## 接口签名

### compress_chain（context.py 暴露给 loop.py）

```python
def compress_chain(
    messages: list[Message],
    tools: list[ToolSpec],
    cfg: CompressionConfig,
    budget: int,
    backend: ModelBackend | None = None,
    model: str = "",
) -> tuple[list[Message], list[LayerReport]]: ...
```

### LayerReport

```python
@dataclass
class LayerReport:
    layer: str                         # stale_snip / microcompact / auto_compact / truncate
    before_tokens: int
    after_tokens: int
    affected: int                      # 被处理的 block / message 数
    skip_thinking: bool = True
    detail: dict = field(default_factory=dict)
```

## Consequences

- 四层每轮独立触发门控，长会话初期零开销
- auto_compact 需要 backend 注入，集成测试用 FakeBackend 可控验证
- microcompact 的 head+tail 行数是硬编码；若后续发现不同工具需要不同 head/tail 大小，可加配置（但暂不超前设计）
- 压缩事件落地后，M2 的"压缩率""正确率"可直接从轨迹事件的 token 字段计算，无需依赖外部日志
- ThinkingBlock 的 token 不纳入压缩阈值——对各厂商链路都安全（高估只会少触发压缩，不会撑爆窗口）

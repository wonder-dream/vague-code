# Model Abstraction

**谁需要读：** 想理解 XClaw 如何统一多厂商 LLM 接口的开发者
**前置阅读：** 05-tool-system.md（了解工具 spec 的跨厂商映射）
**读完能做什么：** 理解 IR 设计、codec 架构、添加新厂商的方法

---

## 1. 概述

各厂商的 LLM API 在消息格式、流式协议、工具调用编码上各不相同。如果上层代码直接面对这些差异，每个特性都要写 N 份分支逻辑。

XClaw 的解法：**自定义 IR（Internal Representation） + 每厂商一个薄 codec**。

IR 的语义照抄 Anthropic content block 模型——四种 Block（text/thinking/tool_use/tool_result）在同一消息中交织排列。这是设计选择：Anthropic 的 content block 模型表达能力最强，其他厂商的格式都可以无损映射到它上面。

上层业务代码（如 `loop.py`）中看不到任何厂商特定的逻辑——它只操作 IR。Codec 负责在 IR 和厂商 wire format 之间做双向转换。

ADR-0002（Custom IR + Codec）、ADR-0005（StreamEvent IR）的设计动机：通过自定义中间表示，将厂商差异隔离在薄 codec 层。

---

## 2. Block 类型

IR 的核心单元是四种 Block（`ir.py:8-83`）：

| Block | dataclass 定义 | to_dict() |
|-------|---------------|-----------|
| TextBlock | `TextBlock(text: str)` | `{"type": "text", "text": "..."}` |
| ThinkingBlock | `ThinkingBlock(text: str, signature: str\|None=None)` | `{"type": "thinking", "text": "...", "signature": "..."}` |
| ToolUseBlock | `ToolUseBlock(id: str, name: str, input: dict)` | `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}` |
| ToolResultBlock | `ToolResultBlock(tool_use_id: str, content: str, is_error: bool=False)` | `{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}` |

**类型联合（`ir.py:83`）：** `Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock`
**BlockType 字面量（`ir.py:8`）：** `BlockType = Literal["text", "thinking", "tool_use", "tool_result"]`

### meta 字段

每个 Block 携带一个 `meta` 字典，用于内部子系统传递元数据：

| Block | meta 用法 | 代码位置 |
|-------|-----------|---------|
| ToolResultBlock | `{"stale": True}` — stale_snip 标记 | `context_compress.py:123-124` |
| ToolResultBlock | `{"compacted": {"original_chars": N, "tool_use_id": "id"}}` — microcompact 标记 | `context_compress.py:194-197` |
| 任意 Block | `{"cache_control": ...}` — 预留 KV Cache 断点 | — |
| 任意 Block | `{"token_estimate": N}` — token 估算 | — |

### 校验规则（`__post_init__`）

- ToolUseBlock：`input` 必须为 `dict`，`id` 和 `name` 非空（`ir.py:48-54`）
- ToolResultBlock：`tool_use_id` 非空（`ir.py:71-73`）

---

## 3. Message / ModelResponse / StopReason / NormalizedUsage

### Message（`ir.py:86-103`）

```python
@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: list[Block]

    def __init__(self, role, content: str | list[Block]):
        # str → [TextBlock(text=str)]
        # list → 直接使用（不允许空列表）
```

`Message` 支持两种构造方式：传入字符串自动包装为 `TextBlock`，或直接传入 Block 列表。空列表禁止。

### StopReason 枚举（`ir.py:106-112`）

| 枚举值 | 厂商映射 | 含义 |
|--------|---------|------|
| `end_turn` | OpenAI `"stop"` / Anthropic `"end_turn"` | 正常完成 |
| `max_tokens` | OpenAI `"length"` / Anthropic `"max_tokens"` | Token 耗尽 |
| `stop_sequence` | Anthropic `"stop_sequence"` | 命中停止序列 |
| `tool_use` | OpenAI `"tool_calls"` / Anthropic `"tool_use"` | 请求工具调用 |
| `content_filter` | OpenAI `"content_filter"` / Anthropic `"refusal"` | 内容被过滤 |
| `unknown` | 默认 | 无法匹配 |

### NormalizedUsage（`ir.py:115-134`）

| 字段 | 类型 | 说明 |
|------|------|------|
| input_tokens | int | 输入 token |
| output_tokens | int | 输出 token |
| cache_read_tokens | int | KV Cache 读取 |
| cache_write_tokens | int | KV Cache 写入 |

non-negative 校验（`ir.py:122-126`）。

### ModelResponse（`ir.py:161-172`）

`message + stop_reason + usage` 的聚合容器——一次 LLM 调用的完整结果。

### ToolSpec（`ir.py:137-158`）

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema

    def to_openai_tool(self) -> dict:     # {"type": "function", "function": {...}}
    def to_anthropic_tool(self) -> dict:  # {"name": ..., "input_schema": ...}
```

`ToolSpec` 提供两个转换方法，将统一的工具定义转换为厂商特定的工具格式。codec 在编码时调用这些方法。

---

## 4. StreamEvent 10 种类型

**代码位置：** `ir.py:175-255`

10 种流事件覆盖了 LLM 流式输出的所有阶段：

| 事件 | 构造签名 | 发生顺序 | 说明 |
|------|---------|---------|------|
| MessageStart | `MessageStart(model: str)` | 1 | 每条 LLM 调用的首事件 |
| ThinkingStart | `ThinkingStart()` | 2 | 可选（仅支持 thinking 的模型） |
| ThinkingDelta | `ThinkingDelta(delta: str)` | 3 | 推理过程中反复产生 |
| ThinkingEnd | `ThinkingEnd(signature: str\|None)` | 4 | — |
| TextDelta | `TextDelta(delta: str)` | 5 | 主文本输出 |
| ToolUseStart | `ToolUseStart(id: str, name: str)` | 6 | — |
| ArgsDelta | `ArgsDelta(id: str, delta: str)` | 7 | 参数 JSON 片段 |
| ToolUseEnd | `ToolUseEnd(id: str)` | 8 | — |
| MessageEnd | `MessageEnd(stop_reason, finish_reason, truncated, usage)` | 9 | 最后一条正常事件 |
| RetryNotice | `RetryNotice(attempt, delay_s, reason)` | 重试时 | 非 LLM 产生，由 Agent 循环制造 |

**类型联合（`ir.py:255`）：** `StreamEvent = MessageStart | ... | RetryNotice`

### Visitor 模式

`dispatch_event()`（`ir.py:277-297`）实现了 Visitor 模式：

```python
def dispatch_event(ev: StreamEvent, v: StreamEventVisitor) -> None:
    if isinstance(ev, MessageStart): v.message_start(ev)
    elif isinstance(ev, ThinkingStart): v.thinking_start(ev)
    ...
```

`StreamEventVisitor` Protocol（`ir.py:264-274`）定义了 10 个方法，CLI 的 `RichStreamVisitor` 实现此接口；TUI v2（ADR-0019）已不再使用 visitor——事件经 `XClawAgentRunner` 回调直达 `TuiTranscript` 单一事实源。

**StreamDisconnect**（`ir.py:258-259`）：流异常断开时抛出，触发重试逻辑。

---

## 5. Codec 架构——双向转换器

每个 codec 是 IR 与厂商 wire format 之间的薄翻译器，实现三个接口：

```
encode_request(messages, tools, config) → dict              # IR → 厂商请求
decode_response(response_dict) → ModelResponse               # 厂商完整响应 → IR
StreamDecoder.decode_chunk(chunk) → list[StreamEvent]        # SSE chunk → StreamEvent
```

### DeepSeek Codec（OpenAI 兼容）

**编码侧（`encode_request()`）：**

| IR | OpenAI 格式 |
|----|------------|
| system message → messages[0] | `{"role": "system", "content": "..."}` |
| assistant ToolUseBlock | `assistant.tool_calls[{id, type:function, function:{name, arguments}}]` |
| user ToolResultBlock | `role: "tool"`, `tool_call_id`, `content` |
| ThinkingBlock | 丢弃 |

特殊处理：
- system 消息合并：多个 system message → 一个 `{"role": "system", "content": "\n\n".join(...)}`
- User 消息的分段：连续同类型 Block 合并，不同类型拆新消息

**解码侧（`decode_response()`）：**
- `reasoning_content` → `ThinkingBlock`（DeepSeek 特有字段）
- `tool_calls` → `ToolUseBlock`（遍历 + JSON.parse arguments）
- `finish_reason` → `StopReason` 映射
- `usage` → `NormalizedUsage`（含 `prompt_tokens_details.cached_tokens` → `cache_read_tokens`）

**流式解码器**（`DeepSeekStreamDecoder`，`codecs/deepseek.py:247-379`）：

状态机字段：`_thinking_open`、`_tools: dict[int, _ToolState]`、`_pending_finish`、`_usage`。

关键逻辑：
- **thinking 边界推断**：`reasoning_content` 出现 → ThinkingStart，消失 → ThinkingEnd
- **tool_call index→id 映射**：SSE 中先发 index 后发 id，需要缓存等待
- **延迟发射**（`_maybe_emit_end()`）：等 usage chunk 到后才发射 MessageEnd
- **防御入口**：`"error" in chunk` → `raise StreamDisconnect`

### Anthropic Codec（Messages API）

**编码侧（`encode_request()`）：**

| IR | Anthropic 格式 |
|----|---------------|
| system message → 顶级 `system` 字段 | `{"system": "..."}` |
| assistant content | `content[{type: text/tool_use/thinking}]` |
| user ToolResultBlock | `content[{type: tool_result, tool_use_id, content, is_error}]` |
| ThinkingBlock | 保留（`type: "thinking"` + `signature`） |

特殊处理：
- **角色交替修复**（`_merge_consecutive_same_role()`）：Anthropic 要求 user/assistant 严格交替，合并连续同角色的消息
- 首条必为 user 校验
- `system` 消息中的非 TextBlock 警告

**流式解码器**（`AnthropicStreamDecoder`，`codecs/anthropic.py:218-334`）：

事件类型驱动：`message_start` → `content_block_start/delta/stop` → `message_delta` → `message_stop`

- **Block 追踪**：`_blocks: dict[int, _BlockState]` 按 index 追踪每个 content block
- **SKIP_BLOCK_TYPES**：过滤 `redacted_thinking`、`server_tool_use` 等不需要暴露给上层的块
- **signature 累积**：`signature_delta` → `ThinkingEnd` 时拼接

### 两 Codec 差异总结

| 特性 | DeepSeek（OpenAI 兼容） | Anthropic（Messages API） |
|------|------------------------|--------------------------|
| system 位置 | `messages[0].role:system` | 顶级 `system` 字段 |
| thinking 处理 | `reasoning_content` → ThinkingBlock，codec 丢弃 | 原生 thinking 块保留 |
| tool 格式 | `tool_calls[{id, type, function}]` | `content[{type: tool_use, id, name, input}]` |
| tool_result 角色 | `role: "tool"` + `tool_call_id` | `role: "user"` + `content[{type: tool_result}]` |
| 角色交替 | 不需要 | 必须（`_merge_consecutive_same_role`） |
| stream 事件 | OpenAI 兼容 SSE | Anthropic event stream |
| prompt caching | `prompt_tokens_details.cached_tokens` | `cache_read_input_tokens` + `cache_creation_input_tokens` |

---

## 6. 添加新提供商——5 步攻略

| 步骤 | 操作 | 参考实现 |
|------|------|---------|
| 1 | 创建 `codecs/new_provider.py`，实现 `encode_request()` + `decode_response()` + StreamDecoder | `codecs/deepseek.py` |
| 2 | 在 `backend.py` 添加 `NewProviderBackend` 类（实现 `ModelBackend` Protocol） | `backend.py:44-91` |
| 3 | 添加 `create_new_provider_backend()` 工厂函数 | `backend.py:94-99` |
| 4 | 更新 `CONTEXT_WINDOWS` 和 `should_skip_thinking()` | `context_tokens.py:14-19` |
| 5 | 编写 golden transcript 测试 | `tests/test_codec_deepseek.py` |

**ModelBackend Protocol**（`backend.py:28-41`）：

```python
class ModelBackend(Protocol):
    def complete(self, messages, tools=None, config=None) -> ModelResponse: ...
    def stream(self, messages, tools=None, config=None) -> Iterator[StreamEvent]: ...
```

这是 XClaw 与 LLM 后端的唯一接口。任何实现了这两个方法的类都可以作为 backend 使用。

---

## 7. Golden Transcript 测试

Golden transcript 是 codec 的核心测试手段：

- **测试流程**：录制真实 SSE chunk stream → 经 codec 解码为 StreamEvent 序列 → 比对 IR 结构
- **验证点**：tool_call id 映射正确、thinking block 保留/丢弃、usage 归一化
- **方式**：`ModelResponse.to_dict()` 快照比对

这种方式不依赖真实的 API 调用，可以在 CI 中零成本验证 codec 的正确性。

---

## 下一篇

→ **10-trajectory.md**：事件溯源与轨迹系统——从事件流到消息数组的完整转换。

**相关 ADR：** 0002（Custom IR + Codec）、0005（StreamEvent IR）
**相关 plans：** 0001（ir-codec）、0005（stream-implementation）、0006（anthropic-codec）

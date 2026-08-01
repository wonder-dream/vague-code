# 细纲：09-model-abstraction.md

**预估行数：** ~500 行
**定位：** 统一 LLM 接口抽象的完整设计。

---

## 开头

- **谁需要读：** 想理解 XClaw 如何统一多厂商 LLM 接口的开发者
- **前置阅读：** 05-tool-system.md（了解工具 spec 的跨厂商映射）
- **读完能做什么：** 理解 IR 设计、codec 架构、添加新厂商的方法

---

## 细纲

### 1. 概述（~40 行）

- 为什么自定义 IR：各厂商 message 格式、streaming 协议、tool call 编码各不相同
- 解法核心：自定义 IR（语义照抄 Anthropic content block 模型）+ 每厂商一个薄 codec
- 上层业务代码零分支的理想——`loop.py` 中看不到任何厂商特定的逻辑
- ADR-0002（Custom IR + Codec）、ADR-0005（StreamEvent IR）设计动机

### 2. Block 类型（~60 行）

**IR 的核心单元——4 种 Block（`ir.py:8-83`）：**

| Block | dataclass 定义 | to_dict() | 附加元数据 |
|-------|---------------|-----------|-----------|
| TextBlock | `TextBlock(text: str)` | `{"type": "text", "text": "..."}` | meta |
| ThinkingBlock | `ThinkingBlock(text: str, signature: str\|None=None)` | `{"type": "thinking", "text": "...", "signature": "..."}` | meta |
| ToolUseBlock | `ToolUseBlock(id: str, name: str, input: dict)` | `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}` | meta |
| ToolResultBlock | `ToolResultBlock(tool_use_id: str, content: str, is_error: bool=False)` | `{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}` | meta |

**Block 类型别名（`ir.py:83 `）：** `Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock`

**BlockType 字面量（`ir.py:8 `）：** `BlockType = Literal["text", "thinking", "tool_use", "tool_result"]`

**meta 字段用途：**
| Block | meta 用法 | 代码位置 |
|-------|-----------|---------|
| ToolResultBlock | `{"stale": True}` — 被 stale_snip 标记 | `context_compress.py:123-124` |
| ToolResultBlock | `{"compacted": {"original_chars": N, "tool_use_id": "id"}}` — 被 microcompact 标记 | `context_compress.py:194-197` |
| 任意 Block | `{"cache_control": ...}` — 预留 KV Cache 断点 | — |
| 任意 Block | `{"token_estimate": N}` — token 估算 | — |

**校验规则（`__post_init__`）：**
- ToolUseBlock：`input` 必须为 `dict`，`id` 和 `name` 非空（`ir.py:48-54`）
- ToolResultBlock：`tool_use_id` 非空（`ir.py:71-73`）

### 3. Message / ModelResponse / StopReason / NormalizedUsage（~60 行）

**`Message`（`ir.py:86-103`）：**
```python
@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: list[Block]

    def __init__(self, role, content: str | list[Block]):
        # str → [TextBlock(text=str)]
        # list → 直接使用（不允许空列表）
```

**`StopReason` 枚举（`ir.py:106-112`）：**

| 枚举值 | 厂商映射 | 含义 |
|--------|---------|------|
| `end_turn` | OpenAI `"stop"` / Anthropic `"end_turn"` | 正常完成 |
| `max_tokens` | OpenAI `"length"` / Anthropic `"max_tokens"` | Token 耗尽 |
| `stop_sequence` | Anthropic `"stop_sequence"` | 命中停止序列 |
| `tool_use` | OpenAI `"tool_calls"` / Anthropic `"tool_use"` | 请求工具调用 |
| `content_filter` | OpenAI `"content_filter"` / Anthropic `"refusal"` | 内容被过滤 |
| `unknown` | 默认 | 无法匹配 |

**`NormalizedUsage`（`ir.py:115-134`）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| input_tokens | int | 输入 token |
| output_tokens | int | 输出 token |
| cache_read_tokens | int | KV Cache 读取 |
| cache_write_tokens | int | KV Cache 写入 |

- non-negative 校验（`ir.py:122-126`）

**`ModelResponse`（`ir.py:161-172`）：** `message + stop_reason + usage` 聚合

**`ToolSpec`（`ir.py:137-158`）：**
```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema

    def to_openai_tool(self) -> dict:    # {"type": "function", "function": {...}}
    def to_anthropic_tool(self) -> dict: # {"name": ..., "input_schema": ...}
```

### 4. StreamEvent 10 种类型及层次结构（~80 行）

**代码位置：** `ir.py:175-255`

| 事件 | 构造签名 | to_dict() | 发生顺序 | 备注 |
|------|---------|-----------|---------|------|
| MessageStart | `MessageStart(model: str)` | `{"stream_type": "message_start", "model": "..."}` | 1 | 每条 LLM 调用的首事件 |
| ThinkingStart | `ThinkingStart()` | `{"stream_type": "thinking_start"}` | 2 | 可选（仅支持 thinking 的模型） |
| ThinkingDelta | `ThinkingDelta(delta: str)` | `{"stream_type": "thinking_delta", "delta": "..."}` | 3 | 推理过程中反复产生 |
| ThinkingEnd | `ThinkingEnd(signature: str\|None)` | `{"stream_type": "thinking_end", "signature": "..."}` | 4 | — |
| TextDelta | `TextDelta(delta: str)` | `{"stream_type": "text_delta", "delta": "..."}` | 5 | 主文本输出 |
| ToolUseStart | `ToolUseStart(id: str, name: str)` | `{"stream_type": "tool_use_start", "id": "...", "name": "..."}` | 6 | — |
| ArgsDelta | `ArgsDelta(id: str, delta: str)` | `{"stream_type": "args_delta", "id": "...", "delta": "..."}` | 7 | 参数 JSON 片段 |
| ToolUseEnd | `ToolUseEnd(id: str)` | `{"stream_type": "tool_use_end", "id": "..."}` | 8 | — |
| MessageEnd | `MessageEnd(stop_reason, finish_reason, truncated, usage)` | `{"stream_type": "message_end", ...}` | 9 | 最后一条正常事件 |
| RetryNotice | `RetryNotice(attempt, delay_s, reason)` | `{"stream_type": "retry_notice", ...}` | 重试时 | 非 LLM 产生，由 Agent 循环制造 |

**类型联合（`ir.py:255 `）：** `StreamEvent = MessageStart | ... | RetryNotice`

**`dispatch_event()`（`ir.py:277-297`）：** Visitor 模式类型分发
```python
def dispatch_event(ev: StreamEvent, v: StreamEventVisitor) -> None:
    if isinstance(ev, MessageStart): v.message_start(ev)
    elif isinstance(ev, ThinkingStart): v.thinking_start(ev)
    ...
```

**`StreamEventVisitor` Protocol（`ir.py:264-274 `）：** 10 个方法，CLI/TUI 渲染器实现此接口

**`StreamDisconnect`（`ir.py:258-259 `）：** 流异常断开时抛出

### 5. Codec 架构——双向转换器（~100 行）

**codec 的职责边界：**

```
encode_request(messages, tools, config) → dict              # IR → 厂商 wire format
decode_response(response_dict) → ModelResponse               # 厂商完整响应 → IR
StreamDecoder.decode_chunk(chunk) → list[StreamEvent]        # SSE chunk → StreamEvent
```

**DeepSeek codec（`codecs/deepseek.py`）：**

**编码侧（`encode_request()`, `_encode_assistant()`, `_encode_user()`）：**

| IR | OpenAI 格式 |
|----|------------|
| system message → messages[0] | `{"role": "system", "content": "..."}` |
| assistant ToolUseBlock | `assistant.tool_calls[{id, type:function, function:{name, arguments}}]` |
| user ToolResultBlock | `role: "tool", tool_call_id, content` |
| ThinkingBlock | 丢弃 |

- system 消息合并：多个 system message → 一个 `{"role": "system", "content": "\n\n".join(...)}`
- User 消息的分段：连续同类型 Block 合并，不同类型拆新消息

**解码侧（`decode_response()`）：**
- `reasoning_content` → `ThinkingBlock`（DeepSeek 特有字段，`codecs/deepseek.py:159-161`）
- `tool_calls` → `ToolUseBlock`（遍历 + JSON.parse arguments）
- `finish_reason` → `StopReason` 映射（`codecs/deepseek.py:208-217`）
- `usage` → `NormalizedUsage`（含 prompt_tokens_details.cached_tokens → cache_read_tokens）

**流式解码器（`DeepSeekStreamDecoder`，`codecs/deepseek.py:247-379`）：**
- 状态机字段：`_thinking_open`、`_tools: dict[int, _ToolState]`、`_pending_finish`、`_usage`
- **thinking 边界推断**（`codecs/deepseek.py:281-291`）：`reasoning_content` 出现→ThinkingStart，消失→ThinkingEnd
- **tool_call index→id 映射**（`codecs/deepseek.py:300-323`）：SSE 中先发 index 后发 id
- **延迟发射**（`_maybe_emit_end()`，`codecs/deepseek.py:357-370`）：等 usage chunk 到后才发射 MessageEnd
- **防御入口**（`codecs/deepseek.py:267-269`）：`"error" in chunk` → `raise StreamDisconnect`

**Anthropic codec（`codecs/anthropic.py`）：**

**编码侧（`encode_request()`, `_merge_consecutive_same_role()`）：**

| IR | Anthropic 格式 |
|----|---------------|
| system message → 顶级 `system` 字段 | `{"system": "..."}` |
| assistant content | `content[{type: text/tool_use/thinking}]` |
| user ToolResultBlock | `content[{type: tool_result, tool_use_id, content, is_error}]` |
| ThinkingBlock | 保留（`type: "thinking"` + `signature`） |

- **角色交替修复**（`_merge_consecutive_same_role()`，`codecs/anthropic.py:89-107`）：Anthropic 要求 user/assistant 交替，合并连续同角色的消息
- 首条必为 user 校验（`codecs/anthropic.py:67-68`）
- `system` 消息中的非 TextBlock 警告（`codecs/anthropic.py:53-59`）

**流式解码器（`AnthropicStreamDecoder`，`codecs/anthropic.py:218-334`）：**
- 事件类型驱动：`message_start` → `content_block_start/delta/stop` → `message_delta` → `message_stop`
- **Block 追踪**：`_blocks: dict[int, _BlockState]` 按 index 追踪每个 content block
- **SKIP_BLOCK_TYPES**（`codecs/anthropic.py:210-215`）：过滤 `redacted_thinking`, `server_tool_use` 等
- **signature 累积**（`codecs/anthropic.py:270-273`）：`signature_delta` → `ThinkingEnd` 时拼接

**两 codec 差异总结表：**

| 特性 | DeepSeek（OpenAI 兼容） | Anthropic（Messages API） |
|------|------------------------|--------------------------|
| system 位置 | `messages[0].role:system` | 顶级 `system` 字段 |
| thinking 处理 | `reasoning_content` → ThinkingBlock，codec 丢弃 | 原生 thinking block 保留 |
| tool 格式 | `tool_calls[{id, type, function}]` | `content[{type: tool_use, id, name, input}]` |
| tool_result 角色 | `role: "tool"` + `tool_call_id` | `role: "user"` + `content[{type: tool_result}]` |
| 角色交替 | 不需要 | 必须（`_merge_consecutive_same_role`） |
| stream 事件 | OpenAI 兼容 SSE | Anthropic event stream |
| prompt caching | `prompt_tokens_details.cached_tokens` | `cache_read_input_tokens` + `cache_creation_input_tokens` |

### 6. 添加新提供商——5 步攻略（~40 行）

| 步骤 | 操作 | 参考实现 |
|------|------|---------|
| 1 | 创建 `codecs/new_provider.py`，实现 `encode_request()` + `decode_response()` + StreamDecoder | `codecs/deepseek.py` |
| 2 | 在 `backend.py` 添加 `NewProviderBackend` 类（实现 `ModelBackend` Protocol） | `backend.py:44-91` `DeepSeekBackend` |
| 3 | 添加 `create_new_provider_backend()` 工厂函数 | `backend.py:94-99` |
| 4 | 更新 `CONTEXT_WINDOWS` 和 `should_skip_thinking()` | `context_tokens.py:14-19` |
| 5 | 编写 golden transcript 测试（固化 SSE → IR 快照） | `tests/test_codec_deepseek.py` |

**`ModelBackend` Protocol（`backend.py:28-41`）：**
```python
class ModelBackend(Protocol):
    def complete(self, messages, tools=None, config=None) -> ModelResponse: ...
    def stream(self, messages, tools=None, config=None) -> Iterator[StreamEvent]: ...
```

### 7. Golden Transcript 测试（~30 行）

- 测试流程：录制真实 SSE chunk stream → 经 codec 解码为 StreamEvent 序列 → 比对 IR 结构
- 验证点：tool_call id 映射正确、thinking block 保留/丢弃、usage 归一化
- `ModelResponse.to_dict()` 快照比对

---

## 结尾

**下一篇推荐：** → 10-trajectory.md（事件溯源与轨迹系统）
**相关 ADR：** 0002（Custom IR + Codec）、0005（StreamEvent IR）
**相关 plans：** 0001（ir-codec）、0005（stream-implementation）、0006（anthropic-codec）

---

## 本文件说明

这是文档 `09-model-abstraction.md` 的细纲（大纲）。写作时需确保 codec 差异表中的每个映射点与实际代码一一对应。`StreamEvent` 10 种类型的顺序需与 `_StreamAggregator.feed()` 的消费逻辑一致。

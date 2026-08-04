# R2：IR 参考

**谁需要读：** 使用 IR 类型进行开发/测试的开发者
**前置阅读：** 09-model-abstraction.md
**读完能做什么：** 理解所有 IR 类型的构造签名、to_dict() 输出格式

---

## 1. Block 4 种类型

**代码位置：** `ir.py:8-83`

| 类型 | 构造签名 | 必填字段 | 可选字段 | to_dict() 输出 |
|------|---------|---------|---------|---------------|
| TextBlock | `TextBlock(text: str)` | text | meta | `{"type": "text", "text": "..."}` |
| ThinkingBlock | `ThinkingBlock(text: str, signature: str\|None=None)` | text | signature, meta | `{"type": "thinking", "text": "...", "signature": "..."}` |
| ToolUseBlock | `ToolUseBlock(id: str, name: str, input: dict)` | id, name, input | meta | `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}` |
| ToolResultBlock | `ToolResultBlock(tool_use_id: str, content: str, is_error: bool=False)` | tool_use_id, content | is_error, meta | `{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}` |

**校验规则（`__post_init__`）：**

| Block | 校验 | ir.py 行 |
|-------|------|----------|
| ToolUseBlock | `input` 必须为 dict | `ir.py:49-50` |
| ToolUseBlock | `id` 非空 | `ir.py:51-52` |
| ToolUseBlock | `name` 非空 | `ir.py:53-54` |
| ToolResultBlock | `tool_use_id` 非空 | `ir.py:71-73` |

**类型别名：**

```python
Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock   # ir.py:83
BlockType = Literal["text", "thinking", "tool_use", "tool_result"]  # ir.py:8
```

**meta 字段内部用途：**

| 用途 | 标记字段 | 写入位置 |
|------|---------|---------|
| stale_snip | `block.meta["stale"] = True` | `context_compress.py:123` |
| compacted 指针 | `block.meta["compacted"] = {"original_chars": N, "tool_use_id": id}` | `context_compress.py:194-197` |
| 原始内容保留 | `block.meta["original_stale_content"] = content` | `context_compress.py:124` |

---

## 2. Message

**代码位置：** `ir.py:86-103`

| 属性 | 类型 | 构造规则 |
|------|------|---------|
| role | `Literal["user", "assistant", "system"]` | — |
| content | `list[Block]` | `str` 自动 wrap 为 `[TextBlock(text=str)]` |

`to_dict()` 输出：`{"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}`

**校验：** content 不能为 None，list 不能为空。

---

## 3. StopReason 枚举

**代码位置：** `ir.py:106-112`

| 枚举值 | 含义 | 对应厂商 finish_reason |
|--------|------|-----------------------|
| `end_turn` | 正常结束 | OpenAI `"stop"` / Anthropic `"end_turn"` |
| `max_tokens` | Token 耗尽 | OpenAI `"length"` / Anthropic `"max_tokens"` |
| `stop_sequence` | 命中停止序列 | Anthropic `"stop_sequence"` |
| `tool_use` | 请求工具调用 | OpenAI `"tool_calls"` / Anthropic `"tool_use"` |
| `content_filter` | 内容被过滤 | OpenAI `"content_filter"` / Anthropic `"refusal"` |
| `unknown` | 无法匹配 | 默认 |

---

## 4. NormalizedUsage

**代码位置：** `ir.py:115-134`

| 属性 | 类型 | 默认值 | 约束 |
|------|------|--------|------|
| input_tokens | int | 0 | ≥ 0 |
| output_tokens | int | 0 | ≥ 0 |
| cache_read_tokens | int | 0 | ≥ 0 |
| cache_write_tokens | int | 0 | ≥ 0 |

`to_dict()`：`{"input_tokens": 1500, "output_tokens": 300, "cache_read_tokens": 0, "cache_write_tokens": 0}`

---

## 5. ToolSpec

**代码位置：** `ir.py:137-158`

| 属性 | 类型 | 说明 |
|------|------|------|
| name | str | 工具名 |
| description | str | LLM 调用决策依据 |
| parameters | dict | JSON Schema |

**输出格式：**
- `to_openai_tool()` → `{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}`
- `to_anthropic_tool()` → `{"name": ..., "description": ..., "input_schema": ...}`

---

## 6. StreamEvent 10 种类型

**代码位置：** `ir.py:175-255`

| 事件 | 构造签名 | to_dict() 输出示例 | 发生顺序 |
|------|---------|-------------------|---------|
| MessageStart | `MessageStart(model: str)` | `{"stream_type": "message_start", "model": "..."}` | 1 |
| ThinkingStart | `ThinkingStart()` | `{"stream_type": "thinking_start"}` | 2 |
| ThinkingDelta | `ThinkingDelta(delta: str)` | `{"stream_type": "thinking_delta", "delta": "..."}` | 3 |
| ThinkingEnd | `ThinkingEnd(signature: str\|None)` | `{"stream_type": "thinking_end", "signature": "..."}` | 4 |
| TextDelta | `TextDelta(delta: str)` | `{"stream_type": "text_delta", "delta": "..."}` | 5 |
| ToolUseStart | `ToolUseStart(id: str, name: str)` | `{"stream_type": "tool_use_start", "id": "...", "name": "..."}` | 6 |
| ArgsDelta | `ArgsDelta(id: str, delta: str)` | `{"stream_type": "args_delta", "id": "...", "delta": "..."}` | 7 |
| ToolUseEnd | `ToolUseEnd(id: str)` | `{"stream_type": "tool_use_end", "id": "..."}` | 8 |
| MessageEnd | `MessageEnd(stop_reason, finish_reason, truncated, usage)` | `{"stream_type": "message_end", "stop_reason": "...", "usage": {...}}` | 9 |
| RetryNotice | `RetryNotice(attempt: int, delay_s: float, reason: str)` | `{"stream_type": "retry_notice", "attempt": 1, "delay_s": 2.5, "reason": "timeout"}` | 重试时 |

**类型联合：** `StreamEvent = MessageStart | ... | RetryNotice`（`ir.py:255`）

**dispatch_event()**（`ir.py:277-297`）：`isinstance` 链式分发到 `StreamEventVisitor` 协议的 10 个方法。

**StreamEventVisitor Protocol**（`ir.py:264-274`）：定义 10 个方法，CLI 的 `RichStreamVisitor` 实现；TUI v2 已弃用 visitor，事件经 `XClawAgentRunner` 回调直达 transcript（ADR-0019）。

**StreamDisconnect**（`ir.py:258-259`）：流异常断开时抛出，触发重试逻辑。

---

## 下一篇

→ **R3：Tool API 参考**——8 个工具的 JSON Schema 定义、参数表、返回值格式。

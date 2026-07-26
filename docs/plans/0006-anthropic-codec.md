# Anthropic Codec + Backend

实现第二厂商（Anthropic Claude）的 codec + backend，支持 `--provider anthropic` 多模型切换。

---

## 设计原则

- **Codec 立**：encode/decode 纯函数，不依赖 backend，可独立单测
- **IR 语义照抄 Anthropic**：codec 是直通映射而非翻译（DeepSeek 才是翻译）
- **多模型统一入口**：Agent Loop 不感知厂商，全部通过 `ModelBackend` protocol 切换

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `src/agent/ir.py` | 改：`ToolSpec.to_anthropic_tool()` |
| 2 | `src/agent/codecs/anthropic.py` | **新建**：encode + decode + stream decoder |
| 3 | `src/agent/backend.py` | 改：加 `AnthropicBackend` + 创建函数 |
| 4 | `src/cli/__init__.py` | 改：加 `--provider` 切换 |
| 5 | `.env.example` | 改：加 `ANTHROPIC_API_KEY` 注释 |
| 6 | `tests/golden/anthropic/` | **新建**：fixture JSON |
| 7 | `tests/test_anthropic_codec.py` | **新建** |

---

## 步骤 1：`ir.py` — `ToolSpec.to_anthropic_tool()`

```python
def to_anthropic_tool(self) -> dict:
    return {
        "name": self.name,
        "description": self.description,
        "input_schema": self.parameters,
    }
```

与 `to_openai_tool()` 的区别：无 `type: "function"` 包装，字段名 `input_schema` 而非 `parameters`。

---

## 步骤 2：`codecs/anthropic.py`

### 2a. 常量与白名单

```python
ALLOWED_CONFIG_KEYS = frozenset({"temperature", "top_p", "max_tokens", "stop_sequences", "metadata", "thinking"})
```

`system` 与 `model` 由 backend 单独处理，不走 codec config 白名单。

### 2b. `encode_request(messages, tools, config)` → dict

**功能**：IR messages → Anthropic Messages API 请求体。

**流程**：
1. 合并连续同角色 message（`user` ↔ `user` → 1 个 `user`，`assistant` ↔ `assistant` → 1 个 `assistant`）
2. 校验 messages 非空，首条为 user
3. 逐条编码（见下）
4. 拼装 body（messages + tools + config filters）

**编码规则**：

| IR Block | Assistant Message | User Message |
|----------|------------------|-------------|
| `TextBlock` | `{"type":"text","text":...}` | `{"type":"text","text":...}` |
| `ToolUseBlock` | `{"type":"tool_use","id":...,"name":...,"input":...}` | — |
| `ToolResultBlock` | — | `{"type":"tool_result","tool_use_id":...,"content":content 或 "(empty)"}` |
| `ThinkingBlock`(有签名) | `{"type":"thinking","thinking":text,"signature":sig}` | — |
| `ThinkingBlock`(无签名) | **跳过**（DeepSeek 用） | **跳过** |

`ToolBlockResult.is_error` → 不传 API。

`content` 为空字符串 → 替换为 `"(empty)"`（Anthropic API 要求非空）。

### 2c. `decode_response(response_dict)` → ModelResponse

**功能**：Anthropic 非流式响应 → IR ModelResponse。

```python
# response_dict 结构（来自 Message.model_dump()）：
{
    "id": "...", "type": "message", "role": "assistant",
    "content": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
        {"type": "thinking", "thinking": "...", "signature": "..."},
    ],
    "model": "claude-opus-4-8",
    "stop_reason": "end_turn" | "max_tokens" | "tool_use" | "stop_sequence" | "refusal" | None,
    "stop_sequence": None | str,
    "usage": {"input_tokens": int, "output_tokens": int, "cache_read_input_tokens": int, "cache_creation_input_tokens": int},
}
```

**映射**：
- `content[].type == "text"` → `TextBlock(text=...)`
- `content[].type == "tool_use"` → `ToolUseBlock(id=..., name=..., input=...)`
- `content[].type == "thinking"` → `ThinkingBlock(text=cb["thinking"], signature=cb["signature"])`
- `redacted_thinking` / `server_tool_use` / unknown → **跳过**
- 空 content → `TextBlock(text="")`

**stop_reason 映射**：

| Anthropic | IR |
|-----------|----|
| `end_turn` | `StopReason.end_turn` |
| `max_tokens` | `StopReason.max_tokens` |
| `tool_use` | `StopReason.tool_use` |
| `stop_sequence` | `StopReason.stop_sequence` |
| `refusal` | `StopReason.content_filter` |
| `None` / `pause_turn` | `StopReason.unknown` |

**usage 映射**：
- `input_tokens` → `NormalizedUsage.input_tokens`
- `output_tokens` → `NormalizedUsage.output_tokens`
- `cache_read_input_tokens` → `NormalizedUsage.cache_read_tokens`
- `cache_creation_input_tokens` → `NormalizedUsage.cache_write_tokens`

### 2d. `AnthropicStreamDecoder`

**状态机**：

```
属性：
  _block_state: dict[int, _BlockState]    # index → {type, id, sig_frags}
  _input_tokens: int                       # 来自 message_start
  _usage: NormalizedUsage                  # 最终合并

事件处理：
message_start:
  → MessageStart(model=message.model)
  → 缓存 message.usage.input_tokens

content_block_start (text):
  → 记录 _block_state[index] = {type: "text", ...}
  → 无 StreamEvent 发射

content_block_start (tool_use):
  → ToolUseStart(id=cb.id, name=cb.name)
  → 记录 _block_state[index] = {type: "tool_use", id: cb.id}

content_block_start (thinking):
  → ThinkingStart()
  → 记录 _block_state[index] = {type: "thinking", sig_frags: []}

content_block_delta (text_delta):
  → TextDelta(delta=delta.text)

content_block_delta (input_json_delta):
  → ArgsDelta(id=index_state.id, delta=delta.partial_json)

content_block_delta (thinking_delta):
  → ThinkingDelta(delta=delta.thinking)

content_block_delta (signature_delta):
  → _block_state[index].sig_frags.append(delta.signature)
  → 无 StreamEvent 发射

content_block_stop:
  → 若 type=="thinking": 拼接 sig_frags → ThinkingEnd(signature=assembled)
  → 若 type=="tool_use": ToolUseEnd(id=_block_state[index].id)
  → 若 type=="text": 无事件
  → 清除 _block_state[index]

message_delta:
  → 缓存 stop_reason + stop_sequence
  → 构造 NormalizedUsage(input_tokens=..., output_tokens=delta.usage.output_tokens,
                          cache_read=delta.usage.cache_read_input_tokens,
                          cache_write=delta.usage.cache_creation_input_tokens)
  → 无 StreamEvent 发射

message_stop:
  → MessageEnd(stop_reason=..., truncated=..., usage=_usage)
```

`flush()` 方法：若 state machine 已在 `ended` 状态或未开始，无操作。用于流中断时的安全兜底。

---

## 步骤 3：`backend.py` — `AnthropicBackend`

```python
class AnthropicBackend:
    def __init__(self, api_key, timeout_s=120.0):
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)

    def complete(self, messages, tools, config):
        body = encode_request(messages, tools, config)
        model = config.get("model", "claude-opus-4-8") if config else "claude-opus-4-8"
        body["model"] = model
        body["max_tokens"] = body.get("max_tokens", 32768)
        response = self._client.messages.create(**body)
        return decode_response(response.model_dump())

    def stream(self, messages, tools, config):
        body = encode_request(messages, tools, config)
        model = config.get("model", "claude-opus-4-8") if config else "claude-opus-4-8"
        body["model"] = model
        body["max_tokens"] = body.get("max_tokens", 32768)
        decoder = AnthropicStreamDecoder()
        with self._client.messages.stream(**body) as msg_stream:
            for event in msg_stream:
                yield from decoder.decode_event(event.model_dump())
```

工厂函数：

```python
def create_anthropic_backend(api_key, timeout_s=120.0) -> AnthropicBackend:
    return AnthropicBackend(api_key=api_key, timeout_s=timeout_s)
```

---

## 步骤 4：CLI `--provider`

加参数：

```python
parser.add_argument("--provider", default="deepseek", choices=["deepseek", "anthropic"])
```

后端选择分支：

```python
if args.provider == "anthropic":
    backend = create_anthropic_backend(api_key=api_key, timeout_s=config.transport.timeout_s)
else:
    backend = create_deepseek_backend(api_key=api_key, base_url="https://api.deepseek.com", timeout_s=config.transport.timeout_s)
```

`_resolve_api_key` 改为按 provider 读不同 env var：

```python
def _resolve_api_key(provider: str) -> str | None:
    env_file = dotenv_values()
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "DEEPSEEK_API_KEY"
    key = env_file.get(key_name)
    if key:
        return key
    import os
    return os.environ.get(key_name)
```

---

## 步骤 5：`.env.example`

```ini
DEEPSEEK_API_KEY=your-deepseek-api-key
ANTHROPIC_API_KEY=your-anthropic-api-key
```

---

## 步骤 6：Golden Fixture

目录 `tests/golden/anthropic/`。

**非流式 fixtures**（每个场景一个 JSON，内容是 `Message.model_dump()` 的输出）：

| File | Content |
|------|---------|
| `text_only.json` | 纯文本响应，`stop_reason: "end_turn"` |
| `single_tool_call.json` | 1 个 `tool_use` content block |
| `multi_tool_call.json` | 2 个并行 `tool_use` + 1 个 `text` |
| `with_thinking.json` | `thinking` + `text` block |

**流式 fixtures**（每行一个 SSE-like JSON event，`/n` 分隔）：

| File | Content |
|------|---------|
| `stream_text_only.jsonl` | message_start → text_deltas → message_delta → message_stop |
| `stream_single_tool.jsonl` | + tool_use block |
| `stream_multi_tool.jsonl` | + 2 tool_use blocks |
| `stream_thinking.jsonl` | + thinking block with signature_delta |

---

## 步骤 7：测试

### encode 测试

| 测试名 | 验证 |
|--------|------|
| `test_encode_empty_raises` | 空 messages → ValueError |
| `test_encode_text_only` | 纯文本往返 |
| `test_encode_tool_use` | assistant 含 ToolUseBlock |
| `test_encode_tool_result` | user 含 ToolResultBlock |
| `test_encode_consecutive_user_merged` | 两个 user → 合并 1 个 |
| `test_encode_unsigned_thinking_skipped` | 无签名 ThinkingBlock → 跳过 |
| `test_encode_empty_content_fallback` | ToolResultBlock.content="" → "(empty)" |
| `test_encode_tools` | ToolSpec → Anthropic 工具格式 |

### decode 测试（golden fixtures）

| 测试名 | Fixture | 验证 |
|--------|---------|------|
| `test_decode_text_only` | `text_only.json` | TextBlock + end_turn |
| `test_decode_single_tool_call` | `single_tool_call.json` | ToolUseBlock |
| `test_decode_multi_tool_call` | `multi_tool_call.json` | 多 ToolUseBlock |
| `test_decode_with_thinking` | `with_thinking.json` | ThinkingBlock(text, signature) |

### 流式测试（golden fixtures）

| 测试名 | Fixture | 验证 |
|--------|---------|------|
| `test_stream_text_only` | `stream_text_only.jsonl` | MessageStart → TextDelta → MessageEnd |
| `test_stream_single_tool` | `stream_single_tool.jsonl` | + ToolUseStart/ArgsDelta/ToolUseEnd |
| `test_stream_multi_tool` | `stream_multi_tool.jsonl` | 多工具正确 index 追踪 |
| `test_stream_thinking` | `stream_thinking.jsonl` | ThinkingStart/Delta/End + signature |

---

## 边界设计决策

| 决策 | 理由 |
|------|------|
| `is_error` 不上 API | 保持一致用 DeepSeek 行为，IR 内部元数据不上线 |
| `max_tokens` 默认 32768 | Claude Opus 4.8 输出上限，coding agent 一次写数百行 |
| `system` 暂不注入 | Week 2 系统提示分层时统一改造两端 codec |
| 无签名 thinking 跳过 | DeepSeek reasoning 无签名，不能发给 Anthropic |
| 空 content → "(empty)" | Anthropic API 会 400 reject 空串 |
| 未知 content block 跳过 | redacted_thinking/server_tool_use 等不支持的类型**

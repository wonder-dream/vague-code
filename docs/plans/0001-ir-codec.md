---
status: implemented
date: 2026-07-20
---

# 0001: 自定义 IR dataclass + DeepSeek codec（v0）

## 目标

完成一次带工具调用的请求-响应往返，使用自定义 IR dataclass（语义照抄 Anthropic content block 模型，ADR-0002）和 DeepSeek codec（OpenAI 兼容协议），非流式。

## 文件结构

```
src/agent/
├── __init__.py
├── ir.py                  # IR dataclass + 辅助类型
└── codecs/
    ├── __init__.py
    └── deepseek.py         # 薄 codec，IR ↔ OpenAI wire format
tests/
├── __init__.py
├── test_deepseek_codec.py  # encode 单测 + golden transcript decode 测试
└── golden/
    ├── text_only.json          # 纯文本回复的原始 API 响应
    ├── text_only.message.json  # 预期的 ModelResponse（IR 序列化）
    ├── single_tool_call.json
    ├── single_tool_call.message.json
    ├── multi_tool_call.json
    └── multi_tool_call.message.json
scripts/
└── v0_roundtrip.py         # 手动验收脚本，走 IR + codec 路径调用真实 API
```

## 实现步骤

### 1. IR dataclass（`src/agent/ir.py`）

Block 类型（Content Block 模型，语义照抄 Anthropic）：

- `TextBlock(text: str)`
- `ThinkingBlock(text: str)` — 推理模型 raw reasoning，IR 层保留但不回传 wire
- `ToolUseBlock(id: str, name: str, input: dict)`
- `ToolResultBlock(tool_use_id: str, content: str, is_error: bool = False)`

Message：

- `role: Literal["user", "assistant"]`
- `content: list[Block]` — 多类型 block 交织在同一 message
- 允许 `str` 便捷构造，内部归一化为单 TextBlock

辅助类型：

- `ToolSpec(name, description, parameters: dict)` — 工具注册 schema
- `StopReason` 枚举：`end_turn | max_tokens | stop_sequence | tool_use | content_filter`
- `NormalizedUsage(input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0)`
- `ModelResponse(message: Message, stop_reason: StopReason, usage: NormalizedUsage)` — codec 出参聚合

序列化：

- 所有 Block 类型（含 `type: Literal` 判别字段）、Message、ModelResponse、NormalizedUsage 实现 `to_dict()`，供黄金快照比对和事件流落盘。`from_dict()` v0 不做。

元数据预留：

- 每个 Block 带 `meta: dict = field(default_factory=dict)`，为后续 stale 标记、折叠状态、event id 预留（ADR-0002 要求，v0 不实现逻辑）

### 2. DeepSeek codec（`src/agent/codecs/deepseek.py`）

三个纯函数 + 一个便捷包装：

- `encode_request(messages: list[Message], tools: list[ToolSpec] | None, config: dict | None) -> dict`

  将 IR messages 列表转换为 OpenAI /chat/completions 请求体。

   **拆分规则**：仅适用于 `role: "user"` 的 IR Message。assistant 消息走独立映射，见下方"其他映射"。

   规则（一次遍历，保持消息级相对顺序）：

   - 每个 IR user Message 的 blocks 数组按"连续同类型"切段，遇类型切换即新段开始。
   - 连续 ToolResultBlock 段 → 每个 block 一条 `role: "tool"` 消息（`tool_call_id` + `content`），段内顺序 = IR 顺序。n 个并行工具结果即 n 条 tool 消息。
   - 连续 TextBlock 段 → 合并为一条 user 角色的消息（文本拼接）。
   - 连续 ThinkingBlock 段 → 丢弃，不上 wire。
   - 段 → 消息的先后顺序严格保持。

   **不变量**：同一 batch 的 ToolResultBlock 必须连续（Agent Loop 构造消息时保证）。codec 不做重排。

   **其他映射**（覆盖所有 role）：

   - assistant message（不切段）：一条 IR assistant 消息 → **一条** wire 消息。TextBlock(s) 拼入 `content`，ToolUseBlock(s) 进入 `tool_calls` 数组（保序），ThinkingBlock 丢弃。
   - user message：不包含 ToolResultBlock 的 user 消息 → 按拆分规则走 TextBlock 段直接合并为一条 wire user 消息（无切段需要）。包含 ToolResultBlock 的 user 消息 → 按拆分规则映射为多条 wire 消息（tool + user 混杂）。
   - 角色交替修复是 Anthropic codec 的职责，DeepSeek codec 不关心。
   - `config` 透传 OpenAI SDK 参数（model, temperature 等）。

  非法结构 → `raise ValueError`（5.7 的保底降级推迟到 Golden 测试暴露真实案例后再定义）。

- `decode_response(response_dict: dict) -> ModelResponse`

  解析 API 响应 JSON 为 ModelResponse：

  - `choices[0].message.content` → TextBlock（有则加）
  - `choices[0].message.reasoning_content` → ThinkingBlock
  - `choices[0].message.tool_calls` → 顺序对应的 ToolUseBlock
  - `choices[0].finish_reason` → StopReason（`stop`→`end_turn`, `tool_calls`→`tool_use`, `length`→`max_tokens`, `content_filter`→`content_filter`）
   - `usage` → NormalizedUsage：`prompt_tokens`→input, `completion_tokens`→output；`completion_tokens_details.reasoning_tokens` 计入 output_tokens 的细分（不在 NormalizedUsage 加字段——评测成本指标统一走 output_tokens）；`prompt_tokens_details.cached_tokens`→cache_read_tokens（DeepSeek 此字段名；cache_write_tokens v0 置 0）。

- `complete(client, messages, tools, config) -> ModelResponse`

  非流式请求包装：encode → `client.chat.completions.create` → decode。

### 3. 单测（`tests/test_deepseek_codec.py`）

**encode 方向**：

- 纯文本 user/assistant → OpenAI messages
- 含 ToolUseBlock 的 assistant → `tool_calls` 数组
- 含 TextBlock + ToolUseBlock 交织 → text 和 tool_calls 独立字段
- user 消息中多个 ToolResultBlock → n 条 role: "tool" 消息
- TextBlock + ToolResultBlock 混合 → 切分为 user + tool 消息（保序）
- 非法 ToolResultBlock（孤立，无前序 ToolUseBlock）→ `ValueError`

**decode 方向（Golden transcript）**：

- 在 `tests/golden/` 目录存放 3 套文件，每套一个原始 API 响应 JSON + 预期 IR 快照：

  - `text_only`：纯文本回复
  - `single_tool_call`：单工具调用，含 reasoning_content
  - `multi_tool_call`：多工具并行

- 测试逻辑：读取原始 JSON → `decode_response` → 取得 `ModelResponse` → 以 `.to_dict()` 序列化为 Python dict → `json.dumps` 比对 `.message.json` 快照。

- **Fixture 录制方式**（v0 手动）：

  1. 用 curl / Python 小脚本以固定 prompt 调用真实 DeepSeek API（纯文本、单 tool_call 带 reasoning_content、多 tool_call 并行）。
  2. **原始响应 JSON 原样保存**为 `tests/golden/<场景>.json`。
  3. 首次运行 decode 测试，人工核对输出无误后，将 `decode_response(fixture).to_dict()` json.dumps 固化为 `<场景>.message.json`。
  4. 后续 codec 行为变更（字段映射、类型判定）与快照不符即比对失败。

**IR 序列化**：所有 Block 类型（含 type 判别字段）、Message、ModelResponse、NormalizedUsage 均实现 `to_dict()`。`from_dict()` v0 不做（无 wire→IR 历史恢复需求）。

### 4. 手动验收（`scripts/v0_roundtrip.py`）

复刻 day0 流程但走 IR + codec：

1. 从 `DEEPSEEK_API_KEY` 环境变量读取 API key
2. 构建 user Message（"读 README.md"）+ ToolSpec（read_file）
3. `encode_request` → 调用 DeepSeek API
4. `decode_response` → 得到 assistant Message（含 ToolUseBlock）
5. 执行工具 → ToolResultBlock
6. 第二轮 encode + decode → 最终文本
7. 打印 usage / stop_reason

## 不做（v0 边界）

- 流式 / StreamEvent IR
- Anthropic codec
- system 消息特殊处理
- 错误重试
- 图片
- structured outputs
- 缓存 / 重试 / 熔断
- codec 对畸形 IR 的保底降级（v0 fail-fast，后续有真实案例再定义）

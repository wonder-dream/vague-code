# 0034: Responses API 支持（OpenAI 官方主推 + Codex 中转站生态）

- **日期**: 2026-08-10
- **状态**: approved（用户确认值得支持）

## 背景

OpenAI 官方将 Responses API 作为主推（最新模型仅经 Responses API 提供，Chat
Completions 为 legacy）；大量 Codex 中转站 `wire_api = "responses"` 只实现该协议。
vague-code 目前仅 Chat Completions，纯 Responses 中转站不可用。

## 设计

- 新增 protocol 值 `"responses"`（配置：`"protocol": "responses"`）
- `codecs/responses.py`：IR → Responses input 映射 / 响应 → IR / 流式事件解码
- `backend.py` 新增 `ResponsesBackend`（openai SDK 的 `client.responses.create/stream`，无新依赖）
- CLI `_build_backend` 按 protocol 分派三路（openai / anthropic / responses）

## 协议要点（已核 SDK 2.46.0）

- input items：`{role, content}` / `{type: "function_call", call_id, name, arguments}`
  / `{type: "function_call_output", call_id, output}`；system → 顶层 `instructions`
- tools：`{type: "function", name, description, parameters}`
- 参数差异：`max_output_tokens`（非 max_tokens）；流式用 `client.responses.stream`
- 流式事件：ResponseTextDeltaEvent → TextDelta；ResponseFunctionCallArgumentsDeltaEvent
  → ArgsDelta；ResponseOutputItemAddedEvent → ToolUseStart；ResponseCompletedEvent
  （携带完整 response，可判定 tool_use/end_turn 与 usage）→ MessageEnd
- ThinkingBlock 不上传（Responses 不支持回传 reasoning，与 GPT 现状一致）

## 改动清单

1. `vague_code/agent/codecs/responses.py`（新）：encode/decode/ResponsesStreamDecoder
2. `vague_code/agent/backend.py`：ResponsesBackend + create_responses_backend
3. `vague_code/cli/__init__.py`：_build_backend 三路分派；`vague_code/config.py`：
   init 模板注释 protocol 选项
4. 测试：codec 编解码 golden、流式事件、backend（mock SDK）、配置 protocol 分派
5. README：方案④ 补充 responses 中转站（protocol: "responses"）

## 验证

全量 pytest + ruff/mypy + 真实中转站 responses 协议冒烟（如可用）。

from __future__ import annotations

import json

from vague_code.agent.ir import (
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 64_000,
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-5": 200_000,
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
}

_SENDS_THINKING_PREFIXES: tuple[str, ...] = ("claude-", "deepseek-")

# GPT 系列使用 OpenAI cl100k 词表（gpt-* / o1-* / o3-* / o4-*）
_GPT_PREFIXES: tuple[str, ...] = ("gpt-", "o1-", "o3-", "o4-")

# Wire-level structural token overhead, measured against the real DeepSeek API
# (trajectory 8c10e58e83dc: local count 111201 vs API input_tokens 121506).
# Each message carries a JSON envelope on the wire that the local count omits.
_WIRE_ENVELOPE_TOKENS = 11  # {"role": "user", "content": ""} outer wrapper
_WIRE_TOOL_RESULT_EXTRA = 10  # tool_call_id field on tool-role messages (21 total)
_WIRE_TOOL_CALL_STRUCT = 40  # nested {"id","type","function":{"name","arguments"}}


def should_skip_thinking(model: str) -> bool:
    """Return True if the model's codec drops ThinkingBlock on the wire (so token budget skips them).
    Return False for models whose codec sends ThinkingBlock (e.g. Anthropic claude, DeepSeek reasoning
    models — DeepSeek 思考模式 + 工具调用要求回传 reasoning_content 并参与上下文计费)."""
    for prefix in _SENDS_THINKING_PREFIXES:
        if model.startswith(prefix):
            return False
    return True


_DS_ENC: object | None = None
_CL100K_ENC: object | None = None
_MODEL_TOK: str = ""


def set_tokenizer_for_model(model: str) -> None:
    """按模型选择 tokenizer（GPT 系列用 cl100k，其余用 DeepSeek 官方词表）。

    与全局 config.model 语义一致：TUI `/model` 切换后下一轮生效。
    """
    global _MODEL_TOK
    _MODEL_TOK = model


def _get_enc():
    """按当前模型返回对应 encoder：GPT 系列 → cl100k；其余 → DeepSeek-V4 官方
    tokenizer（与 API 服务端同一套分词，实测偏差 <1%）。导入失败时 fallback
    到 tiktoken cl100k。
    """
    global _DS_ENC, _CL100K_ENC
    if _MODEL_TOK.startswith(_GPT_PREFIXES):
        if _CL100K_ENC is None:
            try:
                import tiktoken
                _CL100K_ENC = tiktoken.get_encoding("cl100k_base")
            except Exception:
                _CL100K_ENC = False
        return _CL100K_ENC if _CL100K_ENC is not False else None
    if _DS_ENC is None:
        try:
            from deepseek_tokenizer import ds_token
            _DS_ENC = ds_token
        except Exception:
            try:
                import tiktoken
                _DS_ENC = tiktoken.get_encoding("cl100k_base")
            except Exception:
                _DS_ENC = False
    return _DS_ENC if _DS_ENC is not False else None


def count_tokens(
    messages: list,
    tools: list[ToolSpec] | None = None,
    skip_thinking: bool = False,
) -> int:
    enc = _get_enc()
    if enc is not None:
        return _count_precise(messages, tools, enc, skip_thinking)
    return _count_rough(messages, tools, skip_thinking)


def _count_precise(
    messages: list,
    tools: list[ToolSpec] | None,
    enc,
    skip_thinking: bool = False,
) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg
        blocks = content if isinstance(content, list) else []
        has_tool_result = any(isinstance(b, ToolResultBlock) for b in blocks)
        total += _WIRE_ENVELOPE_TOKENS
        if has_tool_result:
            total += _WIRE_TOOL_RESULT_EXTRA
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(enc.encode(block.text))
            elif isinstance(block, ThinkingBlock):
                if not skip_thinking:
                    total += len(enc.encode(block.text))
            elif isinstance(block, ToolUseBlock):
                total += len(enc.encode(block.name))
                total += len(enc.encode(json.dumps(block.input, ensure_ascii=False)))
                total += _WIRE_TOOL_CALL_STRUCT
            elif isinstance(block, ToolResultBlock):
                total += len(enc.encode(block.content))
    if tools:
        for t in tools:
            if t is None:
                continue
            total += len(enc.encode(json.dumps(t.to_openai_tool(), ensure_ascii=False)))
    return total


def _count_rough(
    messages: list,
    tools: list[ToolSpec] | None = None,
    skip_thinking: bool = False,
) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg
        blocks = content if isinstance(content, list) else []
        has_tool_result = any(isinstance(b, ToolResultBlock) for b in blocks)
        total += _WIRE_ENVELOPE_TOKENS
        if has_tool_result:
            total += _WIRE_TOOL_RESULT_EXTRA
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(block.text) // 4
            elif isinstance(block, ThinkingBlock):
                if not skip_thinking:
                    total += len(block.text) // 4
            elif isinstance(block, ToolUseBlock):
                total += len(block.name) // 4
                total += len(json.dumps(block.input, ensure_ascii=False)) // 4
                total += _WIRE_TOOL_CALL_STRUCT
            elif isinstance(block, ToolResultBlock):
                total += len(block.content) // 4
    if tools:
        for t in tools:
            if t is None:
                continue
            total += len(json.dumps(t.to_openai_tool(), ensure_ascii=False)) // 4
    return total


def per_message_tokens(messages: list, skip_thinking: bool = False) -> list[int]:
    """Token count per message (single pass, usable as cache)."""
    enc = _get_enc()
    tokens: list[int] = []
    for msg in messages:
        if enc is not None:
            tokens.append(_count_precise([msg], None, enc, skip_thinking))
        else:
            tokens.append(_count_rough([msg], None, skip_thinking))
    return tokens


def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    window = CONTEXT_WINDOWS.get(model, 64_000)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget

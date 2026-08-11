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
    "deepseek-v4-pro": 1_000_000,
    # OpenAI 现行文本模型（2026-08）：GPT-5.6 系列，1.05M 上下文
    "gpt-5.6": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    # Anthropic 现行模型（2026-08）：Fable 5 / Opus 5 / Sonnet 5 1M，Haiku 4.5 200K
    "claude-fable-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000,
}

_SENDS_THINKING_PREFIXES: tuple[str, ...] = ("claude-", "deepseek-")

# GPT 系列词表（对齐 tiktoken MODEL_TO_ENCODING）：
#   o200k_base：gpt-5.x / gpt-4o / gpt-4.1 / o1 / o3 / o4 及后续新模型
#   cl100k_base：仅老 gpt-4 / gpt-3.5 / gpt2
_GPT_O200K_PREFIXES: tuple[str, ...] = ("gpt-5", "gpt-4o", "gpt-4.1", "o1", "o3", "o4")
_GPT_CL100K_PREFIXES: tuple[str, ...] = ("gpt-4", "gpt-3.5", "gpt-35", "gpt2")


def _window_for(model: str) -> int:
    """上下文窗口：精确匹配 → 系列前缀回退（gpt-5.6* → 1.05M，gpt-5* → 400K，
    claude-* → 1M，deepseek-* → 1M）→ 通用回退。"""
    if model in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[model]
    if model.startswith("gpt-5.6"):
        return 1_050_000
    if model.startswith("gpt-5"):
        return 400_000
    if model.startswith("gpt-"):
        return 128_000
    if model.startswith("claude-"):
        return 1_000_000
    if model.startswith("deepseek-"):
        return 1_000_000
    return 64_000

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
_CLAUDE_ENC: object | None = None
_MODEL_TOK: str = ""


def set_tokenizer_for_model(model: str) -> None:
    """按模型选择 tokenizer（GPT 系列用 cl100k/o200k，Claude 用官方 65K BPE，
    其余用 DeepSeek 官方词表）。
    """
    global _MODEL_TOK
    _MODEL_TOK = model


def _get_enc():
    """按当前模型返回对应 encoder：GPT 系列按词表映射（o200k/cl100k），
    Claude 系列用官方 65K BPE（anthropic-tokenizer），其余用 DeepSeek-V4
    官方 tokenizer（与 API 服务端同一套分词，实测偏差 <1%）。
    导入失败时逐级 fallback：Claude → DeepSeek → tiktoken。
    """
    global _DS_ENC, _CL100K_ENC, _CLAUDE_ENC
    if _MODEL_TOK.startswith(_GPT_O200K_PREFIXES):
        return _get_tiktoken("o200k_base")
    if _MODEL_TOK.startswith(_GPT_CL100K_PREFIXES):
        return _get_tiktoken("cl100k_base")
    if _MODEL_TOK.startswith("gpt-"):
        return _get_tiktoken("o200k_base")  # 未知新 GPT 系列 → 现代词表
    if _MODEL_TOK.startswith("claude-"):
        if _CLAUDE_ENC is None:
            try:
                from anthropic_tokenizer import count_tokens, encode
                _CLAUDE_ENC = (count_tokens, encode)
            except Exception:
                _CLAUDE_ENC = False
        return _CLAUDE_ENC if _CLAUDE_ENC is not False else None
    if _DS_ENC is None:
        try:
            from deepseek_tokenizer import ds_token
            _DS_ENC = ds_token
        except Exception:
            _DS_ENC = _get_tiktoken("o200k_base") or False
    return _DS_ENC if _DS_ENC is not False else None


def _get_tiktoken(name: str):
    """获取 tiktoken 编码（o200k_base/cl100k_base），失败返回 None。"""
    global _CL100K_ENC
    try:
        import tiktoken
        return tiktoken.get_encoding(name)
    except Exception:
        return None


def _count_precise_claude(
    messages: list,
    tools: list[ToolSpec] | None,
    enc,
    skip_thinking: bool = False,
) -> int:
    """Claude 专用计数：anthropic-tokenizer 按字节/BPE 计文本 token。

    官方 65K 词表（Anthropic 未更换词表，claude-1~4 系同一 BPE）；
    未 API 实测对齐（本中转 usage 不可信），基于公开词表事实。
    注意：anthropic-tokenizer 无消息结构计数，这里按与 deepseek 相同的
    wire 开销近似（消息包装 + 工具结构）。
    """
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
                total += enc[0](block.text)
            elif isinstance(block, ThinkingBlock):
                if not skip_thinking:
                    total += enc[0](block.text)
            elif isinstance(block, ToolUseBlock):
                total += enc[0](block.name)
                total += enc[0](json.dumps(block.input, ensure_ascii=False))
                total += _WIRE_TOOL_CALL_STRUCT
            elif isinstance(block, ToolResultBlock):
                total += enc[0](block.content)
    if tools:
        for t in tools:
            if t is None:
                continue
            spec = t.spec if hasattr(t, "spec") else t
            total += enc[0](str(getattr(spec, "name", "")))
            total += enc[0](str(getattr(spec, "description", "")))
            total += enc[0](json.dumps(getattr(spec, "parameters", {}), ensure_ascii=False))
    return total


def count_tokens(
    messages: list,
    tools: list[ToolSpec] | None = None,
    skip_thinking: bool = False,
) -> int:
    enc = _get_enc()
    if enc is None:
        return _count_rough(messages, tools, skip_thinking)
    if _MODEL_TOK.startswith("claude-") and isinstance(enc, tuple):
        return _count_precise_claude(messages, tools, enc, skip_thinking)
    return _count_precise(messages, tools, enc, skip_thinking)


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
    window = _window_for(model)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget

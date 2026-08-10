from __future__ import annotations

from typing import cast

from vague_code.agent.context_tokens import compute_budget, count_tokens
from vague_code.agent.ir import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)


def test_empty_messages_zero() -> None:
    assert count_tokens([]) == 0


def test_single_text() -> None:
    msgs = [Message(role="user", content="hello")]
    assert count_tokens(msgs) > 0


def test_system_message_counted() -> None:
    msgs = [Message(role="system", content="you are an agent")]
    assert count_tokens(msgs) > 0


def test_multi_block_message() -> None:
    msgs = [Message(role="user", content=[
        TextBlock(text="first block"),
        TextBlock(text="second block"),
    ])]
    assert count_tokens(msgs) > 0


def test_tools_included() -> None:
    tools = [
        ToolSpec(
            name="read_file",
            description="Read a file",
            parameters={"type": "object", "properties": {}},
        ),
    ]
    no_tools = count_tokens([])
    with_tools = count_tokens([], tools)
    assert with_tools > no_tools


def test_compute_budget_known_model() -> None:
    assert compute_budget("deepseek-v4-flash") == 900_000
    assert compute_budget("deepseek-v4-pro") == 900_000  # 官方 1M（2026-08 修正）


def test_compute_budget_unknown_model() -> None:
    assert compute_budget("unknown-model") == 57_600  # default 64000 * 0.9


def test_compute_budget_with_user_limit() -> None:
    assert compute_budget("deepseek-v4-flash", user_max_tokens=100_000) == 100_000


def test_compute_budget_user_limit_not_exceeded() -> None:
    assert compute_budget("deepseek-v4-flash", user_max_tokens=2_000_000) == 900_000


def test_count_tokens_none_tool_ignored() -> None:
    bad_tools = cast("list", [None])
    assert count_tokens([], bad_tools) >= 0


def test_count_tokens_fallback_on_tiktoken_missing(monkeypatch) -> None:
    import vague_code.agent.context_tokens as ct

    ct.set_tokenizer_for_model("")
    monkeypatch.setattr(ct, "_DS_ENC", False)
    msgs = [Message(role="user", content="test message")]
    result = count_tokens(msgs)
    assert result > 0


def test_fallback_less_precise_but_reasonable() -> None:
    import vague_code.agent.context_tokens as ct

    ct.set_tokenizer_for_model("")
    saved = ct._DS_ENC
    ct._DS_ENC = False
    try:
        msgs = [Message(role="user", content="abcdefgh" * 100)]
        result = count_tokens(msgs)
        assert result >= 100
    finally:
        ct._DS_ENC = saved


def test_count_tokens_includes_tool_blocks() -> None:
    text_only = [Message(role="user", content="hello")]
    with_tools = [
        Message(role="assistant", content=[
            ThinkingBlock(text="think step by step", signature="sig"),
            TextBlock(text="let me check"),
            ToolUseBlock(id="c1", name="read_file", input={"path": "x.txt"}),
        ]),
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="c1", content="file contents here"),
        ]),
    ]
    # verify non-TextBlock types contribute meaningfully
    base = count_tokens(text_only)
    total = count_tokens(with_tools)
    assert total > base


def test_wire_envelope_overhead_applied() -> None:
    """每个消息的 wire 包装 token 必须计入（修复：本地计数低估 9.3%）。"""
    text = [Message(role="user", content="abc")]
    bare = count_tokens(text)
    envelope = 11  # {"role": "user", "content": ""} 外壳
    assert bare > 0
    assert bare >= len("abc") // 4 + envelope


def test_wire_tool_call_struct_counted() -> None:
    """ToolUseBlock 的嵌套 tool_calls 结构 token 必须计入。"""
    m = [Message(role="assistant", content=[
        ToolUseBlock(id="c1", name="read_file", input={"path": "x.txt"}),
    ])]
    struct = 40  # 嵌套 {"id","type","function":{"name","arguments"}}
    total = count_tokens(m)
    assert total > struct


def test_tools_counted_in_wire_style() -> None:
    """工具定义按 to_openai_tool() 完整 wire 形态计数（含 type/function 包装）。"""
    tools = [
        ToolSpec(
            name="read_file",
            description="Read a file",
            parameters={"type": "object", "properties": {}},
        ),
    ]
    with_tools = count_tokens([], tools)
    wire = tools[0].to_openai_tool()
    import json as _json
    wire_tokens = len(_json.dumps(wire, ensure_ascii=False).encode("utf-8")) // 4
    assert with_tools >= wire_tokens


def test_chinese_tool_args_not_ascii_escaped() -> None:
    """中文参数计数不应因 ensure_ascii 转义虚高。"""
    m = [Message(role="assistant", content=[
        ToolUseBlock(id="c1", name="read_file", input={"path": "中文路径/文件.txt"}),
    ])]
    direct = count_tokens(m)
    # 与 ASCII 等价内容的计数应显著小于转义后的计数
    m_ascii = [Message(role="assistant", content=[
        ToolUseBlock(id="c1", name="read_file", input={"path": "x/y.txt"}),
    ])]
    ascii_tokens = count_tokens(m_ascii)
    assert direct < ascii_tokens * 5


def test_prefers_deepseek_tokenizer_when_available() -> None:
    """默认编码器必须是 DeepSeek-V4 官方 tokenizer（精确计数）。"""
    import vague_code.agent.context_tokens as ct

    enc = ct._get_enc()
    assert enc is not None
    assert getattr(enc, "model_max_length", None) == 1_048_576


def test_fallback_to_tiktoken_when_deepseek_missing(monkeypatch) -> None:
    """deepseek_tokenizer 导入失败时回退 tiktoken，不崩溃。"""
    import vague_code.agent.context_tokens as ct

    ct.set_tokenizer_for_model("")
    monkeypatch.setattr(ct, "_DS_ENC", None)

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "deepseek_tokenizer":
            raise ImportError("no deepseek tokenizer")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    enc = ct._get_enc()
    assert enc is not None
    msgs = [Message(role="user", content="test message")]
    assert count_tokens(msgs) > 0


def test_set_tokenizer_for_model_gpt_uses_o200k() -> None:
    """GPT-4o/4.1/5.x 系列使用 o200k 词表（对齐 tiktoken MODEL_TO_ENCODING）。"""
    import vague_code.agent.context_tokens as ct

    try:
        for model in ("gpt-5.6-sol", "gpt-5.6", "gpt-4o", "gpt-4.1", "o3-mini"):
            ct.set_tokenizer_for_model(model)
            enc = ct._get_enc()
            assert enc is not None, model
            assert getattr(enc, "name", "") == "o200k_base", model
    finally:
        ct.set_tokenizer_for_model("")


def test_set_tokenizer_for_model_legacy_gpt_uses_cl100k() -> None:
    """老 gpt-4/gpt-3.5 系列仍用 cl100k 词表。"""
    import vague_code.agent.context_tokens as ct

    try:
        for model in ("gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"):
            ct.set_tokenizer_for_model(model)
            enc = ct._get_enc()
            assert enc is not None, model
            assert getattr(enc, "name", "") == "cl100k_base", model
    finally:
        ct.set_tokenizer_for_model("")


def test_gpt_compute_budget() -> None:
    """GPT 模型窗口预算：精确 → 系列前缀回退（gpt-5.6* → 1.05M，gpt-5* → 400K）→ 通用回退。"""
    assert compute_budget("gpt-5.6-sol") == 1_050_000 * 0.9
    assert compute_budget("gpt-5.6-terra") == 1_050_000 * 0.9
    assert compute_budget("gpt-5.6") == 1_050_000 * 0.9
    assert compute_budget("gpt-5.6-pro") == 1_050_000 * 0.9
    assert compute_budget("gpt-5.1") == 400_000 * 0.9
    assert compute_budget("gpt-9-future") == 128_000 * 0.9
    assert compute_budget("some-future-model") == 64_000 * 0.9


def test_deepseek_tokenizer_counts_chinese_compactly() -> None:
    """ds_token 对中文按官方 tokenizer 计数（比 cl100k 字节估算紧凑得多）。"""
    import vague_code.agent.context_tokens as ct

    enc = ct._get_enc()
    text = "这是一个中文长句子" * 20
    ds_tokens = len(enc.encode(text))
    byte_estimate = len(text.encode("utf-8")) // 4
    # DeepSeek tokenizer 对中文约 0.6 token/字，应显著低于字节/4 估算
    assert ds_tokens < byte_estimate

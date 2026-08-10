"""benchmark 无交互评测入口测试（ADR-0040）。

覆盖：反作弊提示词关键句、SystemPrompt identity 覆盖、CLI 单次执行成功路径。
"""

from __future__ import annotations

import json

import pytest

from vague_code.agent.context import SystemPrompt, benchmark_identity
from vague_code.agent.ir import Message, ModelResponse, NormalizedUsage, StopReason, TextBlock


class _FakeBackend:
    name = "fake"

    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.call_count = 0

    def complete(self, messages, tools=None, config=None) -> ModelResponse:
        r = self.responses[self.call_count]
        self.call_count += 1
        if isinstance(r, Exception):
            raise r
        return r


def _text_response(text: str = "ok", stop_reason: StopReason = StopReason.end_turn) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=stop_reason,
        usage=NormalizedUsage(input_tokens=5, output_tokens=3),
    )


def _text_response_with_tool() -> ModelResponse:
    from vague_code.agent.ir import ToolUseBlock

    return ModelResponse(
        message=Message(role="assistant", content=[
            ToolUseBlock(id="t1", name="read_file", input={"path": "main.py"}),
        ]),
        stop_reason=StopReason.tool_use,
        usage=NormalizedUsage(input_tokens=5, output_tokens=3),
    )


# ── 反作弊提示词（ADR-0040，对齐 FirstCoder benchmark 提示词）───────────────

def test_benchmark_identity_has_anti_cheat_clauses() -> None:
    """关键反作弊条款必须存在（报告 2.4 样板）。"""
    text = benchmark_identity()
    assert "唯一真相" in text
    assert "不得修改、删除、绕过" in text  # 禁碰测试/verifier/harness
    assert "验证测试" in text
    assert "可观察结果" in text and "才算完成" in text  # 可观察结果才算完成
    assert "不搜索答案" in text or "禁止联网搜索" in text
    assert "不提交" in text or "git commit" in text
    assert "不等待用户输入" in text or "不交互" in text
    assert "bash 仅用于运行测试" in text


def test_system_prompt_identity_override(tmp_path) -> None:
    """SystemPrompt identity 覆盖默认 AGENT_IDENTITY（benchmark 专用提示词）。"""
    custom = benchmark_identity()
    prompt = SystemPrompt(tmp_path, identity=custom).build()
    assert "唯一真相" in prompt
    assert "编码智能体" not in prompt  # 默认身份被替换
    assert "工作目录根路径" in prompt  # 常规部分保留
    default = SystemPrompt(tmp_path).build()
    assert "编码智能体" in default
    assert "唯一真相" not in default


# ── CLI benchmark 入口（单次非交互执行）─────────────────────────────────────

def _patch_env(monkeypatch):
    monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: "sk-fake")
    monkeypatch.setattr(
        "vague_code.cli.sys.exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )


def test_benchmark_runs_single_turn_and_exports(tmp_path, monkeypatch) -> None:
    """benchmark 单次执行：end_turn 正常结束（exit 0）+ 轨迹导出 JSONL。"""
    _patch_env(monkeypatch)

    def fake_build(provider, api_key, base_url, protocol, timeout_s):
        return _FakeBackend([_text_response("done")])

    monkeypatch.setattr("vague_code.cli._build_backend", fake_build)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    out = tmp_path / "traj.jsonl"

    from vague_code.cli import _benchmark_main

    _benchmark_main([
        "--project", str(project),
        "--message", "实现 answer()",
        "--export-jsonl", str(out),
    ])
    assert out.is_file()
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e["type"] == "run_start" for e in lines)
    assert any(e["type"] == "run_end" for e in lines)
    run_end = next(e for e in lines if e["type"] == "run_end")
    assert run_end["payload"]["reason"] == "end_turn"


def test_benchmark_missing_api_key_exits(tmp_path, monkeypatch) -> None:
    """无 API key → stderr 报错并退出。"""
    monkeypatch.setattr("vague_code.cli._resolve_api_key", lambda _: None)
    monkeypatch.setattr(
        "vague_code.cli.sys.exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )

    from vague_code.cli import _benchmark_main

    with pytest.raises(SystemExit) as exc:
        _benchmark_main(["--project", str(tmp_path), "--message", "x"])
    assert exc.value.code == 1


def test_benchmark_failed_run_exits_nonzero(tmp_path, monkeypatch) -> None:
    """run_end 非 end_turn（max_turns 预算耗尽）→ 退出码 1（供评测 harness 归因）。"""
    _patch_env(monkeypatch)

    def fake_build(provider, api_key, base_url, protocol, timeout_s):
        # 首轮返回 tool_use：turn+1 >= max_turns(1) → run_end reason=max_turns
        return _FakeBackend([_text_response_with_tool()])

    monkeypatch.setattr("vague_code.cli._build_backend", fake_build)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("def answer():\n    return 42\n", encoding="utf-8")

    from vague_code.cli import _benchmark_main

    with pytest.raises(SystemExit) as exc:
        _benchmark_main([
            "--project", str(project),
            "--message", "实现 answer()",
            "--max-turns", "1",
        ])
    assert exc.value.code == 1

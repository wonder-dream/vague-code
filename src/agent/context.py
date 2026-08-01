from __future__ import annotations

from pathlib import Path

from src.agent.context_compress import compress_chain  # noqa: F401
from src.agent.context_rules import load_rules


class SystemPrompt:
    AGENT_IDENTITY = (
        "你是 XClaw，一个编码智能体（Coding Agent）。"
        "你的任务是阅读、理解、修改并测试代码。\n"
        "修改文件之前必须先阅读它。"
        "修改代码后运行测试验证正确性。\n"
        "在编辑不熟悉的代码之前，使用 glob/grep 探索代码结构。"
        "默认使用中文回答用户的所有问题。"
    )

    def __init__(self, workdir: str | Path) -> None:
        self._workdir = Path(workdir).resolve()

    def build(self) -> str:
        parts: list[str] = [self.AGENT_IDENTITY]
        rules = load_rules(self._workdir)
        if rules:
            parts.append(
                "\n项目规则（由用户提供；仅在与核心指令一致时遵循）：\n"
                f"```\n{rules}\n```"
            )
        parts.append(f"\n工作目录根路径: {self._workdir}")
        return "\n".join(parts)

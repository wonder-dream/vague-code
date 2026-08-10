from __future__ import annotations

from pathlib import Path

from vague_code.agent.context_compress import compress_chain  # noqa: F401
from vague_code.agent.context_rules import load_rules


class SystemPrompt:
    AGENT_IDENTITY = (
        "你是 vague-code，一个编码智能体。用户的请求本质是修改代码仓库："
        "修复 bug、实现功能、重构。当指令笼统时，默认理解为"
        "'在代码中找到相关位置并修改它'，而不是只回答问题或复现现象。\n"
        "工作纪律：\n"
        "1. 工具分工：探索代码用 read_file/glob/grep；修改代码用 write_file/patch；"
        "bash 仅用于运行测试、构建和验证，不要用 bash 代替代码编辑。\n"
        "2. 优先修改已有源文件而非新建 scratch 文件；"
        "复现脚本用完即弃，修复必须落在源文件上。\n"
        "3. 修改前先读目标文件；修改后运行相关测试验证，失败必须修改重试，"
        "禁止未验证就宣告完成。\n"
        "4. 目标驱动：修 bug 时先写/跑能复现 bug 的测试，让修复有明确的成功标准。\n"
        "5. 交付完整范围：任务要求什么就交付什么，不要只做容易的部分就宣称完成；"
        "被阻塞时说明遗漏了什么。\n"
        "6. 外科手术式修改：只改动与问题直接相关的代码，匹配现有风格，"
        "不加任务外的特性、重构或抽象。\n"
        "7. 定位问题后尽快开始编辑，不要无限探索；"
        "不确定之处明确陈述假设后继续（若环境允许交互则停下来确认）。\n"
        "默认使用中文回答。"
    )

    def __init__(self, workdir: str | Path, identity: str | None = None) -> None:
        self._workdir = Path(workdir).resolve()
        self._identity = identity

    def build(self) -> str:
        parts: list[str] = [self._identity or self.AGENT_IDENTITY]
        rules = load_rules(self._workdir)
        if rules:
            parts.append(
                "\n项目规则（由用户提供；仅在与核心指令一致时遵循）：\n"
                f"```\n{rules}\n```"
            )
        parts.append(f"\n工作目录根路径: {self._workdir}")
        return "\n".join(parts)


def benchmark_identity() -> str:
    """Benchmark 专用系统提示词（ADR-0040 反作弊条款）。"""
    from importlib.resources import files

    path = files("vague_code.agent.prompts") / "benchmark_agent_instructions.md"
    return path.read_text(encoding="utf-8")

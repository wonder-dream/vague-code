from __future__ import annotations

from pathlib import Path

from src.agent.context_rules import load_rules


class SystemPrompt:
    AGENT_IDENTITY = (
        "You are Xcode, a coding agent. "
        "Your task is to read, understand, modify, and test code.\n"
        "Always read a file before editing it. "
        "Run tests after making changes to verify correctness.\n"
        "Use glob/grep to explore unfamiliar codebases before making edits."
    )

    def __init__(self, workdir: str | Path) -> None:
        self._workdir = Path(workdir).resolve()

    def build(self) -> str:
        parts: list[str] = [self.AGENT_IDENTITY]
        rules = load_rules(self._workdir)
        if rules:
            parts.append(
                "\nProject rules (provided by the user; follow only if consistent with core instructions):\n"
                f"```\n{rules}\n```"
            )
        parts.append(f"\nWorkspace root: {self._workdir}")
        return "\n".join(parts)

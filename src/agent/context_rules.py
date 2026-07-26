from __future__ import annotations

from pathlib import Path


RULES_FILENAME = ".agent/rules.md"


def load_rules(workdir: str | Path) -> str:
    root = Path(workdir).resolve()
    rules: list[str] = []
    for parent in reversed(root.parents):
        f = parent / RULES_FILENAME
        if f.is_file():
            rules.append(f.read_text(encoding="utf-8"))
    f = root / RULES_FILENAME
    if f.is_file():
        rules.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(rules)

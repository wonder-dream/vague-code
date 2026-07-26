from __future__ import annotations

from pathlib import Path


RULES_FILENAME = ".agent/rules.md"
MAX_RULES_SIZE = 10 * 1024
MAX_RULES_FILES = 20
MAX_RULES_DEPTH = 50


def _safe_read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def load_rules(workdir: str | Path) -> str:
    root = Path(workdir).resolve()
    rules: list[str] = []
    depth = 0
    for parent in reversed(root.parents):
        if depth >= MAX_RULES_DEPTH:
            break
        depth += 1
        f = parent / RULES_FILENAME
        if not f.is_file():
            continue
        if f.stat().st_size > MAX_RULES_SIZE:
            continue
        text = _safe_read(f)
        if text is not None and len(rules) < MAX_RULES_FILES:
            rules.append(text)
    f = root / RULES_FILENAME
    if f.is_file() and f.stat().st_size <= MAX_RULES_SIZE:
        text = _safe_read(f)
        if text is not None and len(rules) < MAX_RULES_FILES:
            rules.append(text)
    return "\n\n".join(rules)

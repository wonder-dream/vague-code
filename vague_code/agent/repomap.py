from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

_PY_LANGUAGE: object | None = None
_PY_PARSER: object | None = None
_TS_AVAILABLE = False

try:
    from tree_sitter import Language, Parser

    import tree_sitter_python

    _PY_LANGUAGE = Language(tree_sitter_python.language())
    _PY_PARSER = Parser(_PY_LANGUAGE)
    _TS_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when tree-sitter is missing
    _PY_LANGUAGE = None
    _PY_PARSER = None
    _TS_AVAILABLE = False


@dataclass
class Symbol:
    name: str
    kind: str  # "class" | "function" | "method"
    file: str  # relative to workdir
    line: int  # 1-based start line
    signature: str  # e.g. "def foo(a, b) -> int"
    ref_count: int = 0


@dataclass
class RepoIndex:
    workdir: str
    max_files: int = 2000
    _symbols: list[Symbol] = field(default_factory=list, init=False)
    _mtimes: dict[str, float] = field(default_factory=dict, init=False)
    _name_counts: dict[str, int] = field(default_factory=dict, init=False)
    _built: bool = field(default=False, init=False)

    # ── build ────────────────────────────────────────────────────────────

    def build(self, max_files: int | None = None) -> None:
        """Index all supported language files under workdir (mtime-tracked)."""
        root = Path(self.workdir).resolve()
        if not root.is_dir():
            self._built = True
            return
        if max_files is not None:
            self.max_files = max_files

        # 用 os.walk 并在遍历时剪枝噪音/隐藏目录，避免 rglob 先下钻到整棵依赖树
        # 再过滤（大仓库会慢数秒）。语义与原 rglob 版本一致：
        # 忽略隐藏目录（.开头）、__pycache__/node_modules 等噪音目录，以及隐藏 .py 文件。
        _NOISE_DIRS = {"__pycache__", "node_modules", ".idea", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox"}
        py_files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _NOISE_DIRS]
            for fn in filenames:
                if fn.endswith(".py") and not fn.startswith("."):
                    py_files.append(Path(dirpath) / fn)
                    if len(py_files) >= self.max_files:
                        break
            if len(py_files) >= self.max_files:
                break
        py_files = py_files[: self.max_files]

        self._symbols = []
        self._name_counts = {}
        self._mtimes = {}

        for path in py_files:
            try:
                stat = path.stat()
                mtime = stat.st_mtime
            except OSError:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            self._mtimes[rel] = mtime
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            syms, counts = _extract_symbols(text, rel)
            self._symbols.extend(syms)
            for name, c in counts.items():
                self._name_counts[name] = self._name_counts.get(name, 0) + c

        for sym in self._symbols:
            sym.ref_count = self._name_counts.get(sym.name, 0)

        self._built = True

    # ── incremental refresh ──────────────────────────────────────────────

    def refresh(self, paths: list[str] | None = None) -> list[str]:
        """Re-parse changed files (by mtime). Returns list of changed relpaths."""
        root = Path(self.workdir).resolve()
        changed: list[str] = []

        if paths is None:
            candidates = list(self._mtimes.keys())
        else:
            candidates = [p.replace("\\", "/") for p in paths]

        for rel in candidates:
            path = root / rel
            try:
                stat = path.stat()
                mtime = stat.st_mtime
            except OSError:
                continue
            if self._mtimes.get(rel) == mtime:
                continue
            # Drop old symbols from this file, re-extract
            self._symbols = [s for s in self._symbols if s.file != rel]
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                self._mtimes[rel] = mtime
                continue
            syms, counts = _extract_symbols(text, rel)
            self._symbols.extend(syms)
            self._mtimes[rel] = mtime
            changed.append(rel)

        # Recompute reference counts after any change
        self._name_counts = {}
        for relfile in self._mtimes:
            try:
                text = (root / relfile).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            _syms, counts = _extract_symbols(text, relfile)
            for name, c in counts.items():
                self._name_counts[name] = self._name_counts.get(name, 0) + c
        for sym in self._symbols:
            sym.ref_count = self._name_counts.get(sym.name, 0)

        return changed

    # ── query ────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 20, path: str | None = None) -> list[Symbol]:
        """Regex search over symbol name/signature, optional file path filter."""
        if not self._built or not query:
            return []
        try:
            pattern = re.compile(query)
        except re.error:
            return []
        path_pattern = None
        if path:
            try:
                path_pattern = re.compile(path.replace("\\", "/"))
            except re.error:
                return []
        results: list[Symbol] = []
        for sym in self._symbols:
            if path_pattern and not path_pattern.search(sym.file):
                continue
            if pattern.search(sym.name) or pattern.search(sym.signature):
                results.append(sym)
            if len(results) >= k:
                break
        return results

    def top_symbols(self, k: int = 100) -> list[Symbol]:
        """Most-referenced symbols (approximate graph ranking by ref count)."""
        ranked = sorted(self._symbols, key=lambda s: (s.ref_count, s.line), reverse=True)
        return ranked[:k]

    # ── map text generation ──────────────────────────────────────────────

    def to_map_text(self, max_tokens: int = 1000) -> str:
        """Render ``file:line: signature`` lines, top symbols first, within token budget."""
        if not self._built or not self._symbols:
            return ""
        lines: list[str] = []
        used_chars = 0
        budget_chars = max_tokens * 4  # rough estimate: ~4 chars per token
        for sym in self.top_symbols(k=200):
            line = f"{sym.file}:{sym.line}: {sym.signature}"
            cost = len(line) + 1
            if lines and used_chars + cost > budget_chars:
                break
            lines.append(line)
            used_chars += cost
        return "\n".join(lines)

    @property
    def size(self) -> int:
        return len(self._symbols)


# ── symbol extraction ─────────────────────────────────────────────────────

def _extract_symbols(source: str, relpath: str) -> tuple[list[Symbol], dict[str, int]]:
    """Parse Python source, return (symbols, identifier_name_counts)."""
    if not _TS_AVAILABLE:
        return [], {}

    symbols: list[Symbol] = []
    name_counts: dict[str, int] = {}
    parser = _PY_PARSER
    if parser is None:
        return [], {}
    try:
        tree = cast(Any, parser).parse(source.encode("utf-8"))
    except Exception:
        return [], {}

    def _sig_text(node_text: bytes) -> str:
        text = node_text.decode("utf-8", errors="replace")
        first_line = text.splitlines()[0].strip() if text else ""
        # Normalize whitespace inside parens for compactness
        first_line = re.sub(r"\s+", " ", first_line)
        return first_line[:120]

    def walk(node, in_class: bool) -> None:
        if node.type == "class_definition":
            name = _identifier_of(node)
            if name:
                sig = _sig_text(node.text)
                symbols.append(Symbol(
                    name=name, kind="class", file=relpath,
                    line=node.start_point[0] + 1, signature=sig,
                ))
                for child in node.children:
                    walk(child, in_class=True)
                return
        elif node.type == "function_definition":
            name = _identifier_of(node)
            if name:
                kind = "method" if in_class else "function"
                sig = _sig_text(node.text)
                symbols.append(Symbol(
                    name=name, kind=kind, file=relpath,
                    line=node.start_point[0] + 1, signature=sig,
                ))
            for child in node.children:
                walk(child, in_class=in_class)
            return
        elif node.type == "identifier":
            name = source[node.start_byte:node.end_byte]
            name_counts[name] = name_counts.get(name, 0) + 1
        for child in node.children:
            walk(child, in_class=in_class)

    walk(tree.root_node, in_class=False)
    return symbols, name_counts


def _identifier_of(node) -> str | None:
    """Return the defining identifier of a class/function node."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace")
    return None

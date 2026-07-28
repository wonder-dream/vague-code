from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.agent.ir import ToolResultBlock, ToolUseBlock


class OpType(Enum):
    READ = "R"
    WRITE = "W"
    STRUCTURAL_WRITE = "SW"


class ScopeType(Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    WORKSPACE = "workspace"


@dataclass
class ResourceScope:
    path: str
    scope_type: ScopeType
    op_type: OpType


# ── Scope extraction ────────────────────────────────────────────────────────

def _pattern_prefix(pattern: str) -> str:
    """Extract the directory prefix from a glob pattern before the first wildcard."""
    p = pattern.replace("\\", "/")
    # Strip trailing slash for clean comparison
    trimmed = p.rstrip("/")
    m = re.search(r"[*?[\]]", trimmed)
    if m:
        prefix = trimmed[:m.start()]
        if "/" in prefix:
            prefix = prefix.rsplit("/", 1)[0]
        else:
            return ""
        return prefix or ""
    return trimmed or ""


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _extract_scope(call: ToolUseBlock, workdir: str) -> ResourceScope:
    name = call.name
    inp = call.input

    if name == "read_file":
        path = _normalize_path(inp.get("path", ""))
        return ResourceScope(path=path, scope_type=ScopeType.EXACT, op_type=OpType.READ)

    if name == "write_file":
        path = _normalize_path(inp.get("path", ""))
        target = Path(workdir).resolve() / path
        is_new = not target.exists()
        return ResourceScope(
            path=path,
            scope_type=ScopeType.EXACT,
            op_type=OpType.STRUCTURAL_WRITE if is_new else OpType.WRITE,
        )

    if name == "patch":
        path = _normalize_path(inp.get("path", ""))
        return ResourceScope(path=path, scope_type=ScopeType.EXACT, op_type=OpType.WRITE)

    if name == "glob":
        pattern = inp.get("pattern", "")
        prefix = _pattern_prefix(pattern)
        return ResourceScope(path=prefix, scope_type=ScopeType.PREFIX, op_type=OpType.READ)

    if name == "grep":
        path = _normalize_path(inp.get("path") or "")
        return ResourceScope(path=path, scope_type=ScopeType.PREFIX, op_type=OpType.READ)

    return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.WRITE)


# ── Conflict detection ──────────────────────────────────────────────────────

def _scopes_conflict(a: ResourceScope, b: ResourceScope) -> bool:
    if a.op_type == OpType.READ and b.op_type == OpType.READ:
        return False
    if a.scope_type == ScopeType.WORKSPACE or b.scope_type == ScopeType.WORKSPACE:
        return True
    a_path = a.path
    b_path = b.path
    if a_path == b_path:
        return True
    # Empty path means entire workspace (root prefix)
    if a_path and a.scope_type == ScopeType.PREFIX and _path_under(a_path, b_path):
        return True
    if b_path and b.scope_type == ScopeType.PREFIX and _path_under(b_path, a_path):
        return True
    return False


def _path_under(prefix: str, path: str) -> bool:
    """True if `path` is under directory `prefix` (respecting directory boundaries)."""
    if not path.startswith(prefix):
        return False
    if len(path) == len(prefix):
        return True
    return path[len(prefix)] in ("/", "\\")


# ── Scheduler ───────────────────────────────────────────────────────────────

def schedule(
    calls: list[ToolUseBlock],
    workdir: str,
) -> list[list[ToolUseBlock]]:
    scopes = [_extract_scope(c, workdir) for c in calls]
    groups: list[list[ToolUseBlock]] = []
    group_scopes: list[list[ResourceScope]] = []

    for call, scope in zip(calls, scopes):
        placed = False
        for g_calls, g_scopes in zip(groups, group_scopes):
            if not any(_scopes_conflict(scope, gs) for gs in g_scopes):
                g_calls.append(call)
                g_scopes.append(scope)
                placed = True
                break
        if not placed:
            groups.append([call])
            group_scopes.append([scope])

    return groups


# ── Concurrent execution ────────────────────────────────────────────────────

_CONCURRENT_TIMEOUT = 120.0


def execute_concurrent(
    calls: list[ToolUseBlock],
    handlers: dict[str, Callable[[dict], str]],
    workdir: str,
) -> list[ToolResultBlock]:
    groups = schedule(calls, workdir)
    group_scopes = [[_extract_scope(c, workdir) for c in g] for g in groups]
    results: dict[str, ToolResultBlock] = {}
    failed_scopes: list[ResourceScope] = []

    for group, g_scopes in zip(groups, group_scopes):
        if failed_scopes:
            conflict = any(
                any(_scopes_conflict(gs, fs) for fs in failed_scopes)
                for gs in g_scopes
            )
            if conflict:
                for call in group:
                    results[call.id] = ToolResultBlock(
                        tool_use_id=call.id,
                        content="[skipped: cancelled due to upstream failure]",
                        is_error=True,
                    )
                continue

        with ThreadPoolExecutor(max_workers=max(1, min(len(group), 4))) as executor:
            future_map: dict[Future, str] = {}
            group_failed = False
            for call in group:
                handler = handlers.get(call.name)
                if handler is None:
                    results[call.id] = ToolResultBlock(
                        tool_use_id=call.id,
                        content=f"Unknown tool: {call.name}",
                        is_error=True,
                    )
                    group_failed = True
                    continue
                future: Future = executor.submit(handler, call.input)
                future_map[future] = call.id

            try:
                for future in as_completed(future_map, timeout=_CONCURRENT_TIMEOUT):
                    call_id = future_map[future]
                    try:
                        content: str = future.result(timeout=_CONCURRENT_TIMEOUT)
                        results[call_id] = ToolResultBlock(tool_use_id=call_id, content=content)
                    except Exception as e:
                        results[call_id] = ToolResultBlock(
                            tool_use_id=call_id,
                            content=f"{type(e).__name__}: {e}",
                            is_error=True,
                        )
                        group_failed = True
            except TimeoutError:
                group_failed = True
                for call in group:
                    if call.id not in results:
                        results[call.id] = ToolResultBlock(
                            tool_use_id=call.id,
                            content=f"[timed out after {_CONCURRENT_TIMEOUT}s]",
                            is_error=True,
                        )

        if group_failed:
            failed_scopes.extend(g_scopes)

    return [results[c.id] for c in calls]

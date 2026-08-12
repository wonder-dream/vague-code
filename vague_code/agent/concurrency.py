from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed

from vague_code.agent.ir import ToolResultBlock, ToolUseBlock
from vague_code.agent.tools.base import (
    OpType,
    ResourceScope,
    ScopeType,
    Tool,
    ToolResult,
    normalize_path,
    pattern_prefix,
)

# 兼容别名（重构迁移期；新代码直接导入 tools.base）
_normalize_path = normalize_path
_pattern_prefix = pattern_prefix


def _scope_for(call: ToolUseBlock, tools: dict[str, Tool]) -> ResourceScope:
    """并发 scope 由工具实例提供（ADR-0004 重构：元数据内聚，替代按工具名分支）。

    未知工具回退 WORKSPACE + WRITE（与旧默认分支一致）。
    """
    tool = tools.get(call.name)
    if tool is None:
        return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.WRITE)
    return tool.resource_scope(call.input)


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
    tools: dict[str, Tool],
) -> list[list[ToolUseBlock]]:
    scopes = [_scope_for(c, tools) for c in calls]
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
    tools: dict[str, Tool],
) -> list[ToolResultBlock]:
    groups = schedule(calls, tools)
    group_scopes = [[_scope_for(c, tools) for c in g] for g in groups]
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
                        content="[已跳过：因上游失败取消]",
                        is_error=True,
                    )
                continue

        executor = ThreadPoolExecutor(max_workers=max(1, min(len(group), 4)))
        future_map: dict[Future, str] = {}
        group_failed = False
        try:
            for call in group:
                tool = tools.get(call.name)
                if tool is None:
                    results[call.id] = ToolResultBlock(
                        tool_use_id=call.id,
                        content=f"未知工具: {call.name}",
                        is_error=True,
                    )
                    group_failed = True
                    continue
                future: Future = executor.submit(tool, call.input)
                future_map[future] = call.id

            try:
                for future in as_completed(future_map, timeout=_CONCURRENT_TIMEOUT):
                    call_id = future_map[future]
                    try:
                        result: ToolResult = future.result(timeout=_CONCURRENT_TIMEOUT)
                        results[call_id] = ToolResultBlock(
                            tool_use_id=call_id, content=result.output,
                            meta=result.metadata,
                        )
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
                            content=f"[超过 {_CONCURRENT_TIMEOUT} 秒超时]",
                            is_error=True,
                        )
        finally:
            # 超时后立即返回，不等待后台任务（原 with 块会隐式 shutdown(wait=True)）
            executor.shutdown(wait=False, cancel_futures=True)

        if group_failed:
            failed_scopes.extend(g_scopes)

    return [results[c.id] for c in calls]

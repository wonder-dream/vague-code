# 0010: 工具并发调度

实现冲突可串行化的工具并发调度：scope 提取 → 冲突判定 → 分组 → 并发执行。

---

## 设计原则

- **同步风格**：主循环保持同步，ThreadPoolExecutor 做 I/O 并发
- **纯函数调度**：`schedule()` 无副作用，可独立单测
- **失败不可逆转**：上游失败 → 下游跳过，不静默丢弃
- **默认关闭**：`concurrent_tools=False` 保守默认，只影响显式开启的消融实验

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `vague_code/agent/concurrency.py` | **新建**：核心模块 |
| 2 | `vague_code/agent/config.py` | 改：加 `concurrent_tools` 字段 |
| 3 | `vague_code/agent/loop.py` | 改：插入并发路径分支 |
| 4 | `tests/test_concurrency.py` | **新建**：单元 + 集成测试 |

---

## 步骤 1：`concurrency.py`

### 1.1 枚举与数据类

```python
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path
from dataclasses import dataclass

from vague_code.agent.ir import ToolResultBlock, ToolUseBlock


class OpType(Enum):
    READ = "R"
    WRITE = "W"
    STRUCTURAL_WRITE = "SW"  # 新建文件/删除/重命名


class ScopeType(Enum):
    EXACT = "exact"        # 精确文件路径
    PREFIX = "prefix"      # 目录前缀
    WORKSPACE = "workspace"  # 整个工作区


@dataclass
class ResourceScope:
    path: str
    scope_type: ScopeType
    op_type: OpType
```

### 1.2 scope 提取

```python
def _extract_scope(call: ToolUseBlock, workdir: str) -> ResourceScope:
    name = call.name
    inp = call.input

    if name == "read_file":
        path = inp.get("path", "")
        return ResourceScope(path=path, scope_type=ScopeType.EXACT, op_type=OpType.READ)

    if name == "write_file":
        path = inp.get("path", "")
        target = Path(workdir, path).resolve().relative_to(Path(workdir).resolve())
        is_new = not Path(workdir, path).exists()
        ot = OpType.STRUCTURAL_WRITE if is_new else OpType.WRITE
        return ResourceScope(path=str(target), scope_type=ScopeType.EXACT, op_type=ot)

    if name == "patch":
        path = inp.get("path", "")
        return ResourceScope(path=path, scope_type=ScopeType.EXACT, op_type=OpType.WRITE)

    if name == "glob":
        pattern = inp.get("pattern", "")
        # 提取 pattern 的目录前缀
        prefix = _pattern_prefix(pattern)
        return ResourceScope(path=prefix, scope_type=ScopeType.PREFIX, op_type=OpType.READ)

    if name == "grep":
        path = inp.get("path", "")
        return ResourceScope(path=path, scope_type=ScopeType.PREFIX, op_type=OpType.READ)

    # bash, unknown → 保守判为 workspace 级写
    return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.WRITE)


def _pattern_prefix(pattern: str) -> str:
    pattern = pattern.replace("\\", "/")
    # Glob 模式中，第一个通配符 (* ? **) 之前的部分为目录前缀
    import re
    m = re.search(r"[*?[]", pattern)
    if m:
        prefix = pattern[:m.start()]
        # 去掉末尾的 /——让 path.startswith(prefix) 匹配更精确
        if prefix.endswith("/"):
            prefix = prefix.rstrip("/")
        elif "/" in prefix:
            prefix = prefix.rsplit("/", 1)[0]
        else:
            prefix = ""
        return prefix or "."
    # 纯目录路径
    return pattern or "."
```

### 1.3 冲突检测

```python
def _scopes_conflict(a: ResourceScope, b: ResourceScope) -> bool:
    if a.op_type == OpType.READ and b.op_type == OpType.READ:
        return False
    if a.scope_type == ScopeType.WORKSPACE or b.scope_type == ScopeType.WORKSPACE:
        return True
    a_path = a.path
    b_path = b.path
    if a_path == b_path:
        return True
    if a.scope_type == ScopeType.PREFIX and b_path.startswith(a_path):
        return True
    if b.scope_type == ScopeType.PREFIX and a_path.startswith(b_path):
        return True
    return False
```

### 1.4 调度器

```python
def schedule(
    calls: list[ToolUseBlock],
    workdir: str,
) -> list[list[ToolUseBlock]]:
    """将 tool calls 按冲突可串行化分组。组间串行，组内并发。"""
    scopes = [_extract_scope(c, workdir) for c in calls]
    groups: list[list[ToolUseBlock]] = []
    group_scopes: list[list[ResourceScope]] = []

    for call, scope in zip(calls, scopes):
        placed = False
        for i, (g_calls, g_scopes) in enumerate(zip(groups, group_scopes)):
            if not any(_scopes_conflict(scope, gs) for gs in g_scopes):
                g_calls.append(call)
                g_scopes.append(scope)
                placed = True
                break
        if not placed:
            groups.append([call])
            group_scopes.append([scope])

    return groups
```

### 1.5 并发执行

```python
def execute_concurrent(
    calls: list[ToolUseBlock],
    handlers: dict[str, Callable[[dict], str]],
    workdir: str,
) -> list[ToolResultBlock]:
    """调度并并发执行 tool calls。返回结果列表顺序与原 calls 一致。"""
    groups = schedule(calls, workdir)
    results: dict[str, ToolResultBlock] = {}
    failed = False

    for group in groups:
        if failed:
            for call in group:
                results[call.id] = ToolResultBlock(
                    tool_use_id=call.id,
                    content="[skipped: cancelled due to upstream failure]",
                    is_error=True,
                )
            continue

        with ThreadPoolExecutor(max_workers=max(1, min(len(group), 4))) as executor:
            future_map: dict[object, str] = {}
            for call in group:
                handler = handlers.get(call.name)
                if handler is None:
                    results[call.id] = ToolResultBlock(
                        tool_use_id=call.id,
                        content=f"Unknown tool: {call.name}",
                        is_error=True,
                    )
                    failed = True
                    continue
                future = executor.submit(handler, call.input)
                future_map[future] = call.id

            for future in as_completed(future_map):
                call_id = future_map[future]
                try:
                    content = future.result()
                    results[call_id] = ToolResultBlock(tool_use_id=call_id, content=content)
                except Exception as e:
                    results[call_id] = ToolResultBlock(
                        tool_use_id=call_id,
                        content=f"{type(e).__name__}: {e}",
                        is_error=True,
                    )
                    failed = True

    return [results[c.id] for c in calls]
```

---

## 步骤 2：`config.py`

```python
# AgentConfig 新增字段
concurrent_tools: bool = False
```

---

## 步骤 3：`loop.py`

替换 `_run_gen` 中 `tool_uses` 的串行执行循环为并发分支：

```python
if self.config.concurrent_tools:
    from vague_code.agent.concurrency import execute_concurrent

    tool_results = execute_concurrent(tool_uses, bound_tools, workdir)
    for block, result in zip(tool_uses, tool_results):
        traj.emit(EventType.tool_call, turn=turn, payload={
            "id": block.id, "name": block.name, "input": block.input})
        traj.emit(EventType.tool_result, turn=turn, payload={
            "tool_use_id": result.tool_use_id, "content": result.content, "is_error": result.is_error})
    all_results = [ToolResultBlock(tool_use_id=r.tool_use_id, content=r.content, is_error=r.is_error) for r in tool_results]
else:
    # 原串行路径不变
```

---

## 步骤 4：测试

### 测试清单

| 函数 | 测试 | 验证点 |
|------|------|--------|
| `test_scope_extraction_read` | read_file("a.py") → R, EXACT, "a.py" |
| `test_scope_extraction_write_existing` | write_file 已存在 → W, EXACT |
| `test_scope_extraction_write_new` | write_file 新文件 → SW, EXACT |
| `test_scope_extraction_bash` | bash → W, WORKSPACE |
| `test_scope_extraction_glob` | glob "**/*.py" → R, PREFIX, "." |
| `test_no_conflict_read_read` | 两个 read 同一文件 → 不冲突 |
| `test_conflict_read_write_same` | read + write 同一文件 → 冲突 |
| `test_no_conflict_different_paths` | read a.py + write b.py → 不冲突 |
| `test_conflict_with_workspace` | read + bash → 冲突 |
| `test_schedule_all_reads` | 三个不同路径 read → 1 组 |
| `test_schedule_mixed` | read a + write b + bash → 2 组 |
| `test_schedule_all_write_same_file` | 两个 write 同一文件 → 不能并发（2 组） |
| `test_execute_results_order` | 混入多个 call，确认输出顺序与输入一致 |
| `test_execute_failure_propagation` | 组 1 失败 → 组 2 全部 skipped |
| `test_execute_unknown_tool` | handler 缺失 → error + 下游 cancelled |

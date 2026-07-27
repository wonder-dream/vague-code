---
status: accepted
date: 2026-07-27
---

# 0012: 工具并发调度

## 背景

Week 2 最后一块拼图——工具并发调度。当前 Agent 串行执行同批 tool calls（`loop.py:341-356`），模型产出 3 个工具时依次执行，无法利用无关调用之间的并发机会。

约束：并发只能加速，不许改变任何一个 call 观察到的世界（冲突可串行化）。

## 约束

1. **与串行序等价**——LLM 返回 tool call 的顺序是基准串行序，并发的可观察效果必须等价
2. **最小改动 loop.py**——并发层独立为模块，loop.py 只有调用点替换
3. **可消融**——`AgentConfig.concurrent_tools` 默认关闭，实验矩阵遍历
4. **失败传播**——批次 N 任意失败 → 后续批次立即取消（返回 `skipped`），不静默丢弃
5. **不引入 asyncio**——主循环保持同步阻塞风格，用 `concurrent.futures.ThreadPoolExecutor`

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| 并发原语 | A: asyncio / B: ThreadPoolExecutor / C: ProcessPoolExecutor | **B** |
| bash scope 策略 | A: 全部 WORKSPACE / B: 已知只读命令白名单 | **A**（v2 加白名单） |
| 冲突粒度 | A: 文件级精确 / B: 目录前缀 / C: 仅 read vs write | **A + B** |
| 失败传播 | A: 单个 call 失败→同一批其他已提交的不撤回 / B: 整个批次原子失败 | **A** |

## 架构

### 文件职责

```
concurrency.py:              # 新建
  - ResourceScope            # 资源 scope 数据类
  - OpType / ScopeType       # 枚举
  - _extract_scope()         # 从 ToolUseBlock 提取 scope
  - _scopes_conflict()       # 两 scope 冲突判定
  - schedule()               # 分组调度，纯函数
  - execute_concurrent()     # 并发执行 + 结果收集

config.py:                   # 已有，加字段
  - AgentConfig.concurrent_tools

loop.py:                     # 改，替换 tool 执行循环
  - _execute_tools() 调用 execute_concurrent()
```

### 资源 scope 模型

```
每个 call 提取 (path, scope_type, op_type)：
  - scope_type ∈ {EXACT, PREFIX, WORKSPACE}
  - op_type   ∈ {READ, WRITE, STRUCTURAL_WRITE}
```

工具映射：

| 工具 | op_type | scope_type | path 来源 |
|------|---------|------------|----------|
| read_file | R | EXACT | input["path"] |
| write_file(file exists) | W | EXACT | input["path"] |
| write_file(new file) | SW | EXACT | input["path"] |
| patch | W | EXACT | input["path"] |
| glob | R | PREFIX | input["pattern"] 的目录部分 |
| grep | R | PREFIX | input["path"]（默认 workspace root） |
| bash | W | WORKSPACE | — |

### 冲突判定规则

```
if 双方都是 R → 不冲突（两读无危害）
if 任一 scope_type == WORKSPACE → 冲突（保守全串行）
if path 相同 → 冲突
if 一方 PREFIX 且另一方 path 在该 prefix 下 → 冲突
else → 不冲突，可并发
```

### 调度算法

贪心扫描：每个 call 试图加入第一个不冲突的已有分组；无合适分组则新建。

```
输入：call 列表（按模型输出顺序，即基准串行序）
输出：分组列表（组间串行，组内可并发）

for call in calls:
    for existing_group in groups:
        if not any(scopes_conflict(call, member) for member in existing_group):
            existing_group.append(call)
            placed = True
            break
    if not placed:
        groups.append([call])
```

时间复杂度 O(N² × M)，N=call 数，M=已有分组数。N 通常 ≤ 5，可忽略。

### 并发执行

```
for group in groups:
    if failed:                   # 上游失败传播
        for call in group:
            emit skipped
        continue
    with ThreadPoolExecutor as e:
        futures = {submit(handler, input): id for call in group}
        for future in as_completed(futures):
            try get result → emit tool_result
            except Exception → emit error, set failed=True
```

`ThreadPoolExecutor(max_workers=min(len(group), 4))`——限制到 4 防止并发 OOM。

### 失败传播语义

- 同组内：组内一个 call 失败，**同组其他 in-flight call 不撤回**（已提交无法撤销）。失败仅在组边界传播。
- 跨组间：组 N 任意失败 → 组 N+1..M 全部跳过，返回 `[skipped: cancelled due to upstream failure]`

### 与串行路径共存

`AgentConfig.concurrent_tools=False` 时，loop.py 走原串行路径，零性能影响。启动路径依赖只在 `concurrent_tools=True` 时才进。

## Consequences

- 并发工具的非确定性执行顺序对 bash（带副作用）有风险——已用 WORKSPACE 全串行规避
- 线程池执行意味着错误栈不包含 agent 调用方，调试时可从 trajectory 的 tool_result 事件还原
- v2 可加 bash 只读命令白名单提升并发度，但 v1 保守策略足以验证并发收益
- scope 提取器与权限系统的 resource scope 建模思路一致，Week 3 可直接复用

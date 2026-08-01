---
status: proposed
date: 2026-08-01
---

# 0018: Subagent 委派（delegate_task）

## 背景

XClaw 是单 Agent 架构：`Agent(config).run(task, workdir) → Trajectory`。当任务需要理解大项目（如 100 个文件）时，所有内容堆在同一个上下文窗口，工具级并发无法并行探索，五层压缩只能"消化"而非"根治"单上下文压力。主流产品（OpenCode / Cursor / Aider）已具备 subagent 委派能力，这是 XClaw 的差异化缺口。

## 决策

1. **subagent = 嵌套 `Agent.run()`**——ADR-0001（Agent 即库）的红利。子 Agent 复用整个 ReAct 循环、压缩、权限、轨迹系统，只新增一个桥接工具 `delegate_task`。
2. **v1 只读委派**——子 Agent 工具集 `{read_file, glob, grep, code_search, memory_search}`，不含 write/patch/bash。只读是天然信任边界。
3. **防递归**——子 Agent 工具集排除 `delegate_task` 且 `delegate.enabled=False`，最多一层嵌套。
4. **复用并发调度**——`delegate_task` scope 为 `(WORKSPACE, WRITE)`，多个委派走 `execute_concurrent` 线程池。
5. **默认关闭**——`DelegateConfig.enabled=False`，未充分评测前不作为默认路径。

## 约束

1. **零外部服务**——纯本地，复用 ThreadPoolExecutor，不引入 asyncio
2. **只读边界**——v1 子 Agent 不能改文件、不能执行命令
3. **成本硬上限**——`max_turns=8` + `max_subtasks=4`
4. **血缘可追溯**——子 Agent `run_start.parent_run_id` 关联父 run
5. **不接入评测矩阵**——v1 用 FakeBackend 集成测试兜底，避免消融变量爆炸

## 架构

```python
DELEGATE_SPEC = ToolSpec(
    name="delegate_task",
    parameters={"task": str, "files": [str]},
)

def make_delegate_handler(parent_config, backend, memory_store, repo_index):
    def handler(input):
        sub = Agent(deepcopy(parent_config), backend)  # 复用同一引擎/后端
        sub.config.max_turns = 8
        sub.config.delegate.enabled = False            # 防递归
        traj = sub.run(input["task"], workdir)
        return _summarize_trajectory(traj)             # 摘要返回主 Agent
    return handler
```

- **主 Agent 轨迹**：只记 `tool_call(delegate_task)` + `tool_result(摘要)`
- **子 Agent 轨迹**：独立 run_id 事件流，`parent_run_id` 关联
- **结果摘要**：取最后 assistant 文本；无文本兜底 `(完成, reason, 轮次)`

## Consequences

- 上下文从"单窗口堆积"变为"子 Agent 各自消化 + 主 Agent 收摘要"
- 并行探索：多个独立委派并发，复用冲突可串行化
- 面试亮点："Agent 即库"让 subagent 成为桥接工具而非新引擎
- 演进路线：v1 只读 → v2 读写 + patch 回主 → v3 任务自动分解
- 关联实现：`docs/plans/0015-subagent-delegation.md`

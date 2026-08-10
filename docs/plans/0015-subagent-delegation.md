# 0015: Subagent 委派（delegate_task）

新增 Agent 级委派工具 `delegate_task`，让主 Agent 将独立子任务（如"读取大量文件并返回摘要"）交给嵌套的子 Agent 并行执行，解决单上下文压力与探索效率问题。

---

## 背景与动机

### 现状问题

vague-code 是**单 Agent 架构**：`Agent(config).run(task, workdir) → Trajectory`，主 Agent 自己串行执行所有工具调用，所有内容堆在**同一个上下文窗口**里。当任务需要理解大项目（如 100 个文件）时：

1. **上下文爆炸**——100 个文件的全文都在主上下文，即使五层压缩介入也压力大
2. **无法并行探索**——工具级并发（`concurrency.py`）解决的是"一次调多个工具"，但都依赖主 Agent 的循环驱动，探索本身串行
3. **压缩是补丁不是根治**——五层压缩（含 structured_snip）负责"消化"单上下文压力，但 subagent 委派才是根治

对比主流产品（OpenCode / Cursor / Aider）的 subagent 委派模式，vague-code 缺失这块差异化能力。

### 决策来源

1. **ADR-0001（Agent 即库）的红利**：Agent 类是完整的循环引擎。**子 Agent 就是嵌套调用 `Agent.run()`**——复用整个 ReAct 循环、压缩、权限、轨迹系统，只新增一个桥接工具。
2. **与 repo map 互补**：repo map 让主 Agent "少探索"；subagent 让主 Agent "把探索外包"。两者都解决"代码理解效率"。
3. **技术选型**：纯本地、零外部服务（符合零外部服务铁律）；复用现有 `ThreadPoolExecutor` 并发调度，不引入 asyncio。

---

## 核心设计

### 1. 第一性原理：subagent = 嵌套 Agent.run()

```
主 Agent                                 子 Agent（独立实例）
  │                                         │
  ├─ delegate_task("读 a.py..j.py 并摘要") ─→│
  │    handler 创建 sub = Agent(sub_config)  │
  │    sub.run(task, workdir)                │── ReAct 循环（自己的上下文）
  │    ← summarize(traj) ───────────────────│   自己的 5 层压缩
  │    tool_result = 摘要文本                │   自己的轨迹 run_id
```

主 Agent 与子 Agent 用**同一个引擎**，只是配置不同（子 Agent 限轮、限工具）。

### 2. 新工具 `delegate_task`（`vague_code/agent/tools.py`）

**ToolSpec：**

```python
DELEGATE_SPEC = ToolSpec(
    name="delegate_task",
    description="将独立子任务委派给子 Agent 并行执行（v1 只读）。"
                "适合：读取大量文件、并行探索、生成独立分析摘要。"
                "返回子 Agent 的结论摘要。",
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "子任务描述（必填）"},
            "files": {
                "type": "array", "items": {"type": "string"},
                "description": "关注的路径列表（可选，作为子 Agent 起点）",
            },
        },
        "required": ["task"],
    },
)
```

**handler 工厂**（`vague_code/agent/tools.py`）：

```python
def make_delegate_handler(
    parent_config: AgentConfig,
    backend: ModelBackend,
    memory_store,
    repo_index,
) -> Callable[[dict], str]:
    def handler(input: dict) -> str:
        task = input.get("task", "")
        if not task:
            return "需要提供子任务描述。"
        # 子 Agent 配置：继承父配置，但限轮 + 只读工具集 + 禁用委派（防递归）
        sub_config = deepcopy(parent_config)
        sub_config.max_turns = DELEGATE_MAX_TURNS  # 默认 8
        sub_config.tools = readonly_tools()        # read/glob/grep/code_search/memory_search
        sub_config.delegate.enabled = False         # 子 Agent 不能再委派

        sub = Agent(sub_config, backend)
        sub._memory_store = memory_store            # 共享记忆
        sub._repo_index = repo_index                # 共享 repo map（只读）
        traj = sub.run(task, workdir)
        return _summarize_trajectory(traj)
    return handler
```

### 3. 结果摘要（`_summarize_trajectory`）

子 Agent 返回的是 `Trajectory`，不是"答案"。两种提取策略：

- **v1 简单**：取最后一条 assistant 消息的文本（若有）+ `run_end` reason；若无文本，返回 `(完成，reason=end_turn, 轮次=N)`。
- **v1.5 强化**（推荐）：子 Agent 启动时在 system prompt 追加一条"以一句中文总结你的发现"，然后取最后文本。保证子 Agent 主动产出摘要。

### 4. 并发调度（`vague_code/agent/concurrency.py`）

- `delegate_task` 的 scope：`WORKSPACE + WRITE`（与 bash 同级）——**委派期间不与其他写操作并发**。
- 一轮多个 `delegate_task` → 走 `execute_concurrent` 的线程池（复用冲突可串行化在 Agent 粒度）。
- 子 Agent 内部的工具并发：由子 Agent 自己的 `concurrency.py` 独立调度。

### 5. 父子轨迹关联（`vague_code/agent/trajectory.py`）

- 子 Agent 有**自己的 run_id** 事件流。
- 主 Agent 只记一条 `tool_call(delegate_task)` + `tool_result(摘要)`。
- v1 加 `parent_run_id` 字段到 `run_start` payload（子 Agent 记录父 run_id），用于血缘追溯；v2 可做完整父子轨迹树。

### 6. 配置（`vague_code/agent/config.py`）

```python
@dataclass
class DelegateConfig:
    enabled: bool = False          # 默认关闭（未充分评测前）
    max_turns: int = 8             # 子 Agent 轮次上限
    max_subtasks: int = 4          # 单轮最大委派数
    readonly: bool = True          # v1 只读委派

@dataclass
class AgentConfig:
    ...
    delegate: DelegateConfig = field(default_factory=DelegateConfig)
```

### 7. 工具集隔离

子 Agent 的工具集 = **只读工具** `{read_file, glob, grep, code_search}` + `memory_search`（可选）。**不含** `write_file`、`patch`、`bash`、`delegate_task`：

- **安全**：子 Agent 不能改文件、不能执行命令（v1 只读信任边界）
- **防递归**：子 Agent 不能委派（`delegate_task` 不在其工具集）

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `vague_code/agent/config.py` | 加 `DelegateConfig` + `AgentConfig.delegate` |
| 2 | `vague_code/agent/tools.py` | 加 `DELEGATE_SPEC` + `make_delegate_handler()` + `readonly_tools()` |
| 3 | `vague_code/agent/concurrency.py` | `_extract_scope` 加 `delegate_task` → `(WORKSPACE, WRITE)` |
| 4 | `vague_code/agent/loop.py` | `start()` 动态注册 `delegate_task`（当 `delegate.enabled`）；handler 闭包注入 backend/memory/repo_index |
| 5 | `vague_code/agent/trajectory.py` | `run_start` payload 加可选 `parent_run_id` |
| 6 | `vague_code/agent/memory.py` | 无需改（共享 memory_store 已是线程安全） |
| 7 | `vague_code/cli/__init__.py` | 加 `--delegate` / `--no-delegate` / `--delegate-max-turns` |
| 8 | `tests/test_delegate.py` | **新建**：委派闭环 / 只读工具集 / 递归防护 / 摘要提取 / scope 冲突 |
| 9 | `docs/plans/0015-subagent-delegation.md` | **新建**（本文档） |
| 10 | `docs/adr/0018-subagent-delegation.md` | **新建** ADR |

---

## 测试计划

**单元**（`tests/test_delegate.py`）：

- `delegate_task` handler：用 FakeBackend 子 Agent 跑通，返回摘要文本
- 只读工具集：子 Agent 的 `_tool_specs` 不含 write/patch/bash/delegate_task
- 递归防护：子 Agent 配置 `delegate.enabled=False` 且工具集无 delegate_task
- 摘要提取：有文本 → 取最后 assistant 文本；无文本 → 兜底 `(完成, reason, 轮次)`
- 空 task → 返回错误消息
- scope 冲突：`delegate_task` 与其他 WORKSPACE 操作冲突判定正确
- 父子轨迹：`run_start.parent_run_id` 正确写入

**集成**：FakeBackend 下主 Agent 委派 2 个子 Agent，验证并发执行 + 各自轨迹落盘。

**质量门**：ruff + mypy 零错误，pytest 全绿（516 → 约 535 条）。

---

## 预期收益

- **上下文分流**：100 个文件的探索从"主上下文堆积"变为"N 个子 Agent 各自消化，主 Agent 只收摘要"
- **并行探索**：多个独立子任务并行（复用冲突可串行化），减少主 Agent 串行轮次
- **面试差异化**："Agent 即库"架构红利——subagent 不是新引擎而是桥接工具，这是可讲的架构亮点

---

## 已知风险

| 风险 | 缓解 |
|------|------|
| 子 Agent 结果质量不稳定 | v1 只读委派 + `_summarize_trajectory` 兜底；`delegate.enabled=False` 默认关闭 |
| 成本失控（每个子 Agent 独立 LLM 调用） | `max_turns=8` + `max_subtasks=4` 硬上限 |
| 共享 backend 线程安全 | openai/anthropic 客户端线程安全；`check_same_thread=False` 已处理 |
| 递归委派 | 子 Agent 工具集排除 `delegate_task` + `delegate.enabled=False` |
| 与评测矩阵耦合 | v1 不接入 eval 矩阵（避免变量爆炸）；独立 FakeBackend 集成测试兜底 |

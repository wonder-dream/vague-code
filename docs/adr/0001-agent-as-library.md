---
status: accepted
date: 2026-07-19
---

# Agent 核心作为 Python 库，CLI 仅为薄壳

Agent Runtime 暴露 `Agent(config).run(task, workdir) → Trajectory` 编程接口，CLI 只是这个库的一个前端。这是评测 harness 能够以编程方式控制实验变量的前提——如果 Agent 是 subprocess 黑盒，控制变量只能通过 CLI 参数和环境变量注入，内部指标（压缩事件、工具耗时、token usage）采集需额外解析，且 30 题 × 3 变量 × 3 重复 = 270 次运行时进程创建开销不可接受。

## Considered Options

- **纯 CLI + subprocess 启动（被否决）**：实现简单但评测时控制变量手段脏、内部指标采集脆、启动开销累积大。subprocess 保留用于 E2E 冒烟测试（验证 CLI 本身未损坏）。
- **Agent 即库（选定）**：所有上层消费者（CLI、评测 harness、未来的 IDE 插件）共享同一编程接口。config 对象一次性注入实验变量，返回结构化的 Trajectory 供后续分析。

## Consequences

- 所有模块（Agent Loop、工具系统、上下文工程、权限、记忆）必须作为 Python 包的一部分，通过 `AgentConfig` 暴露配置项，不能依赖 CLI 全局状态；
- CLI 变为薄壳：解析参数 → 构造 `AgentConfig` → 调用 `Agent(config).run()` → 渲染输出。CLI 本身不包含任何 Agent 逻辑；
- 评测 harness 以 import 方式驱动 Agent，每个任务在独立 git worktree 或临时目录中执行以隔离副作用；
- 新增配置项时需同步更新 `AgentConfig` dataclass 和 CLI 参数映射，但核心逻辑只写一次。

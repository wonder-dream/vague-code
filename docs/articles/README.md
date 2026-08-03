# XClaw 文档

共 **24 篇**成品文章，按推荐阅读路径分为 6 个阶段。

---

## 推荐阅读路径

### Phase 0 — 概念入门（零基础可读）

| 顺序 | 文章 | 读完能做什么 |
|------|------|-------------|
| 00 | [What Is a Coding Agent?](00-what-is-a-coding-agent.md) | 理解 XClaw 是什么、和 Copilot/ChatGPT 的区别 |
| 01 | [Terminology](01-terminology.md) | 掌握所有核心术语 |
| 02 | [Architecture Overview](02-architecture-overview.md) | 理解 10 个子系统的协作关系 |

### Phase 1 — 架构全景

| 顺序 | 文章 | 读完能做什么 |
|------|------|-------------|
| 03 | [A Single Turn Explained](03-a-single-turn-explained.md) | 读懂 `loop.py` 的完整调用链 |

### Phase 2 — 子系统深潜

| 顺序 | 文章 | 核心主题 |
|------|------|---------|
| 04 | [Agent Runtime](04-agent-runtime.md) | ReAct 循环、重试、Checkpoint/Resume |
| 05 | [Tool System](05-tool-system.md) | 8 个工具（6 基础 + 2 动态）、并发模型、安全性 |
| 06 | [Context Engineering](06-context-engineering.md) | 五层压缩、Token Budget |
| 07 | [Permission System](07-permission-system.md) | 四级安全模式、三层规则 |
| 08 | [Memory System](08-memory-system.md) | Episodic 检索、蒸馏、热度排序 |
| 09 | [Model Abstraction](09-model-abstraction.md) | IR、Codec、10 种 StreamEvent |
| 10 | [Trajectory](10-trajectory.md) | 事件溯源、SQLite 存储、Resume |
| 11 | [CLI and TUI](11-cli-and-tui.md) | Rich 渲染、Textual 界面、线程桥接 |
| 12 | [Evaluation Harness](12-evaluation-harness.md) | 消融实验、FakeBackend、报告 |

### Phase 3 — 动手教程

| 顺序 | 文章 | 实践内容 |
|------|------|---------|
| T1 | [Your First Task](T1-your-first-task.md) | 安装到跑通第一个任务 |
| T2 | [Fixing a Real Bug](T2-fixing-a-real-bug.md) | 观察完整修 Bug 流程 |
| T3 | [Extending XClaw](T3-extending-xclaw.md) | 添加工具/厂商/评测任务 |
| T4 | [Running Ablation Experiments](T4-running-ablation-experiments.md) | 配置矩阵、FakeBackend、报告解读 |

### Phase 4 — API 参考（按需查阅）

| 文章 | 内容 |
|------|------|
| [R1: AgentConfig](R1-agent-config.md) | 5 个配置类的所有字段 |
| [R2: IR Reference](R2-ir-reference.md) | Block、Message、StreamEvent 等 IR 类型 |
| [R3: Tool API](R3-tool-api.md) | 8 个工具的 JSON Schema 和边界 |
| [R4: Trajectory Events](R4-trajectory-events.md) | 12 种事件的 payload 和 SQL 查询 |
| [R5: CLI Reference](R5-cli-reference.md) | 所有 flag、键绑定、斜杠命令 |

### Phase 5 — 补充

| 文章 | 内容 |
|------|------|
| [Troubleshooting](troubleshooting.md) | 7 类常见问题与解决方案 |
| [FAQ](faq.md) | 12 个设计决策问答 |

---

## 相关目录

| 目录 | 内容 |
|------|------|
| [docs/adr/](../adr/) | 18 篇架构决策记录 |
| [docs/plans/](../plans/) | 16 篇实现方案（含 0016 评测体系补强） |
| [docs/handoff/](../handoff/) | 会话交接记录（含 2026-08-03 评测体系全量总结） |
| [docs/audit/](../audit/) | 5 篇代码审计报告 |
| [docs/reviews/](../reviews/) | 1 篇代码审查 |
| [docs/blog/](../blog/) | 1 篇技术博客 |
| [docs/interview/](../interview/) | 面试设计问题 |
| [eval/](../../eval/) | 评测工具（现行用法见 `eval/README.md`） |

# Architecture Decision Records

ADR（Architecture Decision Record）是 XClaw 的架构决策记录。每份 ADR 记录一个关键设计决策的背景、备选方案、最终选择和后果。

状态说明：
- **accepted** — 已采纳并实现
- **proposed** — 已提议，待评审
- **superseded** — 被后续 ADR 取代
- **deprecated** — 已废弃不再使用

---

## ADR 索引表

| 编号 | 主题 | 状态 | 关联文档 | 关联 plans |
|------|------|------|---------|-----------|
| 0001 | Agent 即库（薄 CLI） | accepted | 04, 11, 12 | 0002 |
| 0002 | 自定义 IR + Codec | accepted | 09 | 0001, 0005, 0006 |
| 0003 | 事件溯源轨迹 | accepted | 10 | 0002-section |
| 0004 | 工具注册 + Factory | accepted | 05 | — |
| 0005 | 统一 StreamEvent + Visitor | accepted | 09, 11 | 0005 |
| 0006 | 重试 + Checkpoint/Resume | accepted | 04 | 0003 |
| 0007 | System Prompt 分层注入 | accepted | 06 | 0007 |
| 0008 | 规则文件层级加载 | accepted | 06 | — |
| 0009 | Token Budget 计算 | accepted | 06 | — |
| 0010 | 上下文模块架构 | accepted | 06 | — |
| 0011 | 五层压缩流水线 | accepted | 06, blog/compression.md | 0008, 0013 |
| 0012 | 工具并发调度 | accepted | 05 | 0010 |
| 0013 | 权限系统 | accepted | 07 | 0011 |
| 0014 | 跨会话记忆 | accepted | 08 | 0012 |
| 0015 | TUI 架构 | accepted | 11 | — |
| 0016 | Repo Map 代码库符号索引 | accepted | — | 0014 |
| 0017 | 轨迹驱动结构化压缩层 | accepted | 06 | 0013 |
| 0018 | Subagent 委派（delegate_task） | proposed | 04 | 0015 |
| 0019 | TUI v2 分层重写（参考包架构） | accepted | 11 | 0017 |

---

## 按文档查找 ADR

| 文档 | 相关 ADR |
|------|---------|
| 04-agent-runtime | 0001, 0006 |
| 05-tool-system | 0004, 0012 |
| 06-context-engineering | 0007, 0008, 0009, 0010, 0011 |
| 07-permission-system | 0013 |
| 08-memory-system | 0014 |
| 09-model-abstraction | 0002, 0005 |
| 10-trajectory | 0003 |
| 11-cli-and-tui | 0001, 0015, 0019 |
| 12-evaluation-harness | 0001 |

---

**相关链接：** [docs/adr/](../adr/) 目录（15 篇完整 ADR 文件）

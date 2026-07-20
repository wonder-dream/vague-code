---
status: accepted
date: 2026-07-19
---

# 轨迹存储使用事件流（Event-Sourced JSONL），messages 数组仅作导出格式

Agent 每次运行的轨迹以 event-sourced JSONL 存储（每行一个类型化事件），而非直接存 OpenAI-compatible messages 数组。messages 数组通过 `to_messages()` 从事件流导出，作为 LLM-as-Judge 的输入。

messages 数组天然丢失以下评测和消融实验必需的信息：重试记录（哪次 tool call 是重试）、压缩事件（哪层压缩触发、回收多少 token）、权限拦截决策（操作被 deny、用户点了确认还是放行）、每轮时间戳和 token usage、工具执行耗时。而这些恰好是第 2.2 节全部量化指标的数据源。先有完整事实，再选择视角导出——这个顺序不可逆。

事件流存储于 SQLite（runs 表 + events 表），支持离线重算：judge 提示词改版、失败重分类、新增派生指标——以上全都不需要重跑 Agent。这一点对迭代评测至关重要——如果评测需要重跑 Agent，每轮实验设计的修改都会触发 270 次 API 调用。

## Considered Options

- **直接存 messages 数组（被否决）**：有损存储，重试、压缩、权限事件全部丢失，无法回答"为什么这次失败了"的根因分析。
- **dual-write 双写事件流 + messages（被否决）**：两条路径保持一致的复杂度高，且 messages 只是事件流的投影——从事件流导出即可得到。
- **纯事件流 + 导出转换器（选定）**：存储层只维护一份事实源；导出层按需投影为 messages 数组或其他消费者格式。

## Consequences

- 所有模块（Agent Loop、压缩流水线、权限、工具并发调度）需在关键节点 emit 类型化事件，事件 schema 需在事件字典中统一定义；
- 评测 harness 的 metrics 计算全部从 events 表 SQL 查询得出，不依赖 Agent 运行时的 side-channel 输出；
- `to_messages()` 导出为 OpenAI-compatible messages 数组，供 LLM-as-Judge 和轨迹可视化消费——导出逻辑是纯函数，judge 提示词改版不需要重跑 Agent；
- SQLite 作为事件存储：runs 表（run_id, config_hash, task_id, status, created_at）+ events 表（run_id, turn, ts, type, payload JSON）。schema 演化走 `payload` JSON 加字段，不改表结构；
- 事件流是评测与 Agent 之间的唯一数据契约——Agent 修改内部行为时只要事件不变，评测侧无需同步改动。

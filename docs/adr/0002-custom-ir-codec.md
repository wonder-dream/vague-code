---
status: accepted
date: 2026-07-19
---

# 自定义 dataclass IR + 厂商 thin codec 架构

模型抽象层使用自定义 dataclass 作为内部表示（IR），语义照抄 Anthropic 的 content block 模型（text / tool_use / tool_result 交织在同一 message 内）。每厂商一个 thin codec（200~400 行）负责 IR ↔ wire format 的双向转换。上层业务代码（Agent Loop、ContextManager、权限、评测、日志）不见任何厂商特定类型。

选择 Anthropic 语义作为 IR 基础的理由：Anthropic 的 content block 模型是超集（支持同一 message 内多类型 block 交织），向 OpenAI 格式投影比反向投影损失小；且 tool_use/tool_result 与 message 的绑定关系天然保持并发 batch 边界（一个 assistant message 内的多个 tool_use = 一个并发 batch）。

选择自定义类型而非直接复用 Anthropic SDK 类型的理由：上下文工程需要携带 stale 标记、折叠状态和原文指针；权限和审计需要关联 event id；budget 记账需要 block 级 token 估算——这些都是任何厂商协议不存在的元数据。直接用 Anthropic SDK 类型加猴子补丁同样会变成事实上的自定义类型，且没有 schema 边界。

## Considered Options

- **以 OpenAI 协议为 IR（被否决）**：分离式 tool_calls 字段无法自然表示工具调用与文本推理交织的 message，且 Anthropic → OpenAI 格式投影需要拆分 assistant message，丢失 batch 边界语义。
- **完全厂商无关的最小公分母 IR（被否决）**：会丢失每家后端的独特能力（Anthropic 的 prompt caching、OpenAI 的 structured outputs），且映射层需要双向补丁，维护成本高于两套独立 codec。
- **自定义 IR + 语义照抄 Anthropic（选定）**：每次新增厂商后端只需写一个 codec，上层零改动。Anthropic/OpenAI 协议演进趋同时 codec 自动变薄。

## Consequences

- 所有上层模块（ContextManager、权限检查、事件日志、评测回放）只操作 IR dataclass，不 import 任何厂商 SDK 类型；
- ContextManager 直接操作 IR block：snip = 删除 block + 插入标记 block，cache_control 断点写入 block 元数据，不需感知后端差异；
- 厂商特有能力通过 `AgentConfig.extra: dict` 透传（如 Anthropic 的 `thinking`、OpenAI 的 `response_format`），由 codec 解释；
- thinking/reasoning：IR 保留 block，Anthropic codec 原样回传，OpenAI codec 丢弃——这是已知限制，写进 README；
- 流式工具参数增量 JSON 拼接逻辑只存在于 StreamEvent IR 层，codec 各自实现 `parse_delta` 适配器；
- 新增厂商后端 = 新增一个 codec 文件（~300 行），Agent 核心零改动；
- 厂商 API 格式变更时，Golden transcript 快照测试第一时间告警，修复局限在对应 codec。

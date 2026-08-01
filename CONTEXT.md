# Coding Agent (XClaw)

面向真实编码场景的轻量级本地 Coding Agent CLI，具备自研 Agent Runtime、可控工具系统、分层上下文治理、权限安全体系、跨会话记忆，以及配套的自动化评测工具链。

## Language

**Agent Runtime**:
Agent 的主循环引擎：接收任务，驱动 LLM 推理 → 工具调用 → 观察反馈的 ReAct 循环，管理流式输出、重试、超时熔断和检查点。暴露 `Agent(config).run(task, workdir) → Trajectory` 编程接口。
_Avoid_: Agent Loop Engine, Agent Core

**Tool System**:
Agent 与外部世界交互的唯一接口。每个工具注册时声明 JSON Schema、资源类型（filesystem:read/write, process, network）和操作语义（R/W）。工具调用由冲突可串行化模型调度并发执行。
_Avoid_: Plugin System, Capability

**Context Engineering**:
管理 LLM 上下文窗口的子系统。五层压缩流水线（stale_snip → microcompact → structured_snip → auto_compact → truncation）按精准度排序回收 token，系统提示分层注入以命中 KV Cache。
_Avoid_: Prompt Engineering, Context Management

**Permission System**:
保护工作区安全的子系统。4 种模式（safe/normal/autoedit/auto）按操作可逆性切分信任等级，三层规则（持久/会话级/单次豁免）提供 allow/deny 覆盖，deny 优先级最高。决策函数为纯函数，每次决策落审计日志。
_Avoid_: Security Module, Safety Guard

**Memory System**:
跨会话知识存储与检索子系统。统一记忆库 + episodic（按需检索）注入策略。写入走会话蒸馏（auto-compact 协同），检索走 LIKE + 热度排序召回。
_Avoid_: Knowledge Base, RAG

**Repo Map**:
代码库符号索引子系统。基于 tree-sitter 的本地解析，提供 `code_search` 工具 + 符号地图注入，解决代码理解能力与主流产品（Aider repo map / Cursor 向量索引）的差距。
_Avoid_: Vector Index, Code Search DB

**Model Abstraction Layer**:
统一 LLM 后端接入层。自定义 dataclass IR（语义照抄 Anthropic content block 模型）+ 每厂商一个薄 codec，上层代码零分支。流式事件统一为 StreamEvent IR。
_Avoid_: API Layer, Provider Adapter

**Trajectory**:
一次 Agent 运行的完整记录。存储为 event-sourced JSONL（每行一个类型化事件），可通过 to_messages() 导出为 messages 数组供 LLM-as-Judge 消费。与评测工具的桥梁数据结构。
_Avoid_: Run Record, Session Log

**Evaluation Harness**:
评测系统的控制层：读取实验矩阵配置，以编程方式驱动 Agent（控制自变量），收集轨迹（测量因变量），运行验收脚本判定 pass/fail，产出对比报告。不是离线日志分析器。
_Avoid_: Benchmark Runner, Test Suite

**Compression Pipeline**:
上下文工程的核心机制。五层顺序：stale_snip（删消息流内被后续操作覆盖的旧文件读取）→ microcompact（对超长工具输出做结构化摘要，保留原文指针）→ structured_snip（利用轨迹事件识别已完成子任务，零成本替换为结构化摘要）→ auto_compact（利用率 > 85% 时全量会话摘要）→ truncation（硬截断兜底）。
_Avoid_: Context Pruning, Summarization

**Conflict Serializability**:
工具并发调度的理论基础。LLM 返回 tool call 的顺序为基准串行序，并发执行必须在可观察效果上等价。资源 scope 重叠且至少一方是写即串行，否则并发。
_Avoid_: Dependency Detection, Parallel Execution

**Event Stream**:
轨迹的存储格式。每行一个 JSON 对象，携带事件类型、时间戳、token usage、延迟等消息数组天然丢失的元数据。存储于 SQLite（runs 表 + events 表），支持离线重算和失败重分类。
_Avoid_: Log File, Telemetry

**IR (Internal Representation)**:
模型抽象层的内部数据类型。语义照抄 Anthropic content block（text/thinking/tool_use/tool_result 四种 block 交织在同一 message），携带内部元数据（stale 标记、折叠状态、cache_control 断点、token 估算、event id）。配套类型：`StopReason` 枚举（end_turn/max_tokens/tool_use/...）、`NormalizedUsage`（统一 token 成本记账）、`ModelResponse`（聚合 message + usage + stop_reason）。所有类型实现 `to_dict()` 序列化，供 golden transcript 快照比对和事件流落盘。
_Avoid_: Message Format, API Schema

**Codec**:
IR 与厂商 wire format 之间的双向转换器。每个 LLM 后端一个 codec，负责 tool_call id 映射、消息角色交替修复（Anthropic 侧）、thinking/reasoning 厂商亲和处理、usage 统计归一化。上层业务代码不见 codec。
_Avoid_: Adapter, Connector, Driver

**Ablation Experiment**:
控制变量法验证设计决策的实验：固定其他变量，开关单一特性（压缩/记忆/并发），对比 pass rate、token 消耗、正确率等因变量。消融实验的结果是简历上所有百分比数字的数据源。
_Avoid_: A/B Test, Benchmark Run

**Fail-to-Pass / Pass-to-Pass**:
SWE-bench 的验收测试双清单概念。修 bug 任务必须使 fail-to-pass 测试从挂到过，同时 pass-to-pass 测试不回归。保证"修了一个 bug 没引入新的 bug"。
_Avoid_: Regression Test, Acceptance Test

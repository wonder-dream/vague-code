# 细纲：01-terminology.md

**预估行数：** ~300 行
**定位：** 随时回来查的术语字典。分组排列，先通俗后精准。

---

## 开头

- **谁需要读：** 阅读后续文档前的必读参考，不熟悉 Coding Agent 概念的读者
- **前置阅读：** 00-what-is-a-coding-agent.md
- **读完能做什么：** 理解所有核心术语的通俗含义和正式定义，能正确使用术语进行交流

---

## 细纲

### 分组 1：Agent 层（~40 行）

| 术语 | 一句话通俗解释 | 正式定义 | 常见错误叫法 |
|------|--------------|---------|-------------|
| Agent Runtime | Agent 的"大脑"，负责循环思考→行动 | 主循环引擎，驱动 ReAct 循环，管理重试/超时熔断/检查点，暴露 `Agent(config).run() → Trajectory` 接口（`loop.py:159-184`） | Agent Core、Engine |
| Turn（轮次） | Agent 的一轮"思考→行动"周期 | 从 LLM 返回结果到所有工具执行完毕的一个完整周期（`loop.py:250-486`） | Round、Step |
| Trajectory（轨迹） | Agent 一次运行的完整"黑匣子"记录 | event-sourced JSONL 事件流，可通过 `to_messages()` 导出为消息数组供消费（`trajectory.py:121-124`） | Run Record、Log |
| ReAct Loop | "思考→行动→观察→再思考"的循环模式 | Reasoning + Acting 交替执行的 LLM agent 范式（`loop.py:241-489`） | Chain-of-Thought |
| Checkpoint（检查点） | 自动存档，断了可以从中继续 | `Trajectory.persist()`（`loop.py:491-496`）将当前状态写入 SQLite，支持 `Agent.resume()` 恢复 | Snapshot |

### 分组 2：工具层（~40 行）

| 术语 | 一句话通俗解释 | 正式定义 | 常见错误叫法 |
|------|--------------|---------|-------------|
| Tool Spec | 工具的"说明书"——名称、参数、返回值 | JSON Schema 定义的工具接口（`ir.py:138-150`），注册时声明资源类型和操作语义 | Plugin Config |
| Resource Scope | 工具操作的"势力范围" | 三维度（scope_type × path × op_type）描述操作的影响范围（`concurrency.py:25-30`） | Permission Scope |
| Conflict Serializability | 同时执行多个工具时确保结果等价于逐个执行 | 以 LLM 返回的 tool call 顺序为基准串行序，scope 重叠且有写者为串行，否则并发（`concurrency.py:90-104`） | Dependency Detection |
| 8 个工具（6 基础 + 2 动态） | Agent 与外部世界交互的唯一接口 | read_file / write_file / patch / glob / grep / bash（基础 6）+ memory_search / code_search（动态 2） | — |

### 分组 3：压缩层（~50 行）

| 术语 | 一句话通俗解释 | 正式定义 |
|------|--------------|---------|
| Token Budget（预算） | 上下文窗口的"费用上限" | 模型 context_window × 0.9（`context_tokens.py:131-136`） |
| stale_snip | 删掉被后序同路径读取覆盖的旧文件内容 | 第一层，纯规则，零 LLM 成本（`context_compress.py:69-136`） |
| microcompact | 把超长工具输出折叠为头尾摘要 | 第二层，head+tail 结构化折叠，保留原文指针（`context_compress.py:151-208`） |
| auto_compact | 用 LLM 自身总结历史对话 | 第三层，利用率 > 85% 触发（`context_compress.py:221-351`） |
| truncation（截断） | 硬截断——最后的兜底 | 保留 system + task + 最新消息（`context_compress.py:356-484`） |
| KV Cache | LLM 逐 token 推理时，前文算过的注意力结果不重算——只算增量 | Transformer 自回归推理的注意力 Key/Value 张量缓存。每生成一个 token，其 K、V 被保存；后续 token 只计算对已缓存 K/V 的增量注意力，将每步 O(n²) 降为 O(n)。发生在单次请求内部，对 API 调用者透明 |
| Prompt Caching | 多次请求之间，前缀重复的部分只编码一次——省 token 省延迟 | LLM 提供商基于底层 KV Cache 的跨请求缓存复用。服务端持久化请求前缀的 K/V 张量，新请求前缀匹配时跳过重复编码直接复用。Anthropic 通过 `cache_control` 显式标记断点，DeepSeek/OpenAI 自动前缀检测。XClaw 三段式 system prompt 将不变的 identity 段固定首部最大化命中（ADR-0007） |

### 分组 4：权限层（~35 行）

| 术语 | 通俗解释 | 定义 |
|------|---------|------|
| Permission Mode（模式） | 安全等级 | safe / normal / autoedit / auto，按操作可逆性切分（`permission.py:14-18`） |
| Decision（决策） | 权限判定的结果 | ALLOW / CONFIRM / DENY（`permission.py:8-11`） |
| Audit Log（审计日志） | 每次权限决策都记录 | `permission_check` 事件（`loop.py:519-522`） |
| 三层规则体系 | 持久规则 → 会话规则 → 单次豁免 | DENY 最高优先级（`permission.py:33-37`） |

### 分组 5：记忆层（~30 行）

| 术语 | 通俗解释 | 定义 |
|------|---------|------|
| Pinned Memory（常驻记忆）[已废弃] | 已移除 | 原常驻注入职责由 `.agent/rules.md`（ADR-0008）承担 |
| Episodic Memory（情景记忆） | 按需检索的历史经验 | `memory_search` 工具，LIKE 查询 + 热度排序（`memory_tool.py:5-19`） |
| Distillation（蒸馏） | 自动从对话中提取新记忆 | auto_compact 摘要作为新 episodic 记忆入库（`loop.py:323-329`） |

### 分组 6：模型层（~40 行）

| 术语 | 通俗解释 | 定义 |
|------|---------|------|
| IR（内部表示） | 统一的消息内部格式 | 自定义 dataclass，语义照抄 Anthropic content block 模型（`ir.py:8-83`，4 种 Block） |
| Codec（编解码器） | IR 与厂商格式的翻译器 | 每个 LLM 后端一个薄翻译层，上层业务代码零分支（`codecs/deepseek.py`、`codecs/anthropic.py`） |
| Block（内容块） | 消息的最小内容单元 | text / thinking / tool_use / tool_result（`ir.py:8-83`） |
| StreamEvent（流事件） | 统一的流式事件类型 | 10 种事件（`ir.py:255`），含 MessageStart、TextDelta、ToolUseStart 等 |

### 分组 7：评测层（~30 行）

| 术语 | 通俗解释 | 定义 |
|------|---------|------|
| Ablation Experiment（消融实验） | 开关某一特性对比效果 | 固定其他变量，开关单一特性（压缩/记忆/并发），对比 pass rate 等因变量 |
| Fail-to-Pass | 修 Bug 后原来挂的测试必须通过 | 保证 bug 被真正修复 |
| Pass-to-Pass | 修 Bug 后原来过的测试不能挂 | 保证"修了一个 bug 没引入新的 bug" |
| FakeBackend | 模拟 LLM 后端，零 API 成本验证框架 | 始终返回 `TextBlock("ok")` + `stop_reason=end_turn`（`harness.py:129-141`） |

---

## 结尾

**下一篇推荐：** → 02-architecture-overview.md（把 10 个子系统串起来理解）
**相关链接：** CONTEXT.md（机器可读术语规范）

---

## 本文件说明

这是文档 `01-terminology.md` 的细纲（大纲）。实际写作时每个术语条目保持三段式：① 一句话通俗解释 ② 正式定义（含代码位置）③ 常见错误叫法（如有）。所有引用均以实际代码为准。

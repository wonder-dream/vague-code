# Terminology

**谁需要读：** 阅读后续文档前的必读参考，不熟悉 Coding Agent 概念的读者
**前置阅读：** 00-what-is-a-coding-agent.md
**读完能做什么：** 理解所有核心术语的通俗含义和正式定义，能正确使用术语进行交流

---

## 分组 1：Agent 层

### Agent Runtime

- **一句话通俗解释：** Agent 的"大脑"，负责循环"思考→行动"。
- **正式定义：** 主循环引擎，驱动 ReAct 循环（Reasoning + Acting），管理重试、超时熔断和检查点。对外暴露 `Agent(config).run(task, workdir) → Trajectory` 编程接口（`loop.py:159-184`）。
- **常见错误叫法：** Agent Core、Engine——不要用，关键在 Runtime 作为"运行时环境"。

### Turn（轮次）

- **一句话通俗解释：** Agent 的一轮"思考→行动"周期。
- **正式定义：** 从 LLM 返回响应到所有工具执行完毕的一个完整周期。一个 task 通常包含多个 turn（`loop.py:250-486`）。
- **常见错误叫法：** Round、Step——Turn 是标准术语。

### Trajectory（轨迹）

- **一句话通俗解释：** Agent 一次运行的完整"黑匣子"记录。
- **正式定义：** event-sourced JSONL 事件流，每行一个类型化事件。可通过 `to_messages()` 导出为消息数组供 LLM-as-Judge 消费（`trajectory.py:121-124`）。与评测工具的桥梁数据结构。
- **常见错误叫法：** Run Record、Session Log——Trajectory 强调事件溯源性质。

### ReAct Loop

- **一句话通俗解释：** "思考→行动→观察→再思考"的循环模式。
- **正式定义：** Reasoning + Acting 交替执行的 LLM agent 范式。Agent Runtime 的核心循环（`loop.py:241-489`）。
- **常见错误叫法：** Chain-of-Thought——CoT 只有推理没有行动，ReAct 是推理+行动交替。

### Checkpoint（检查点）

- **一句话通俗解释：** 自动存档，断了可以从中继续。
- **正式定义：** `Trajectory.persist()`（`loop.py:491-496`）将当前状态写入 SQLite，支持 `Agent.resume()` 从断点恢复。存在两个 persist 点：每次 tool 调用后和每次 turn 结束后。
- **常见错误叫法：** Snapshot——Snapshot 是文件系统概念，Checkpoint 是 Agent 执行中的状态。

---

## 分组 2：工具层

### Tool Spec（工具规约）

- **一句话通俗解释：** 工具的"说明书"——名称、参数、返回值。
- **正式定义：** JSON Schema 定义的工具接口（`ir.py:138-150`），注册时声明资源类型（filesystem:read/write、process、network）和操作语义（R/W）。
- **常见错误叫法：** Plugin Config——Tool Spec 是函数式规约，不是插件配置。

### Resource Scope（资源范围）

- **一句话通俗解释：** 工具操作的"势力范围"——它读/写了什么文件。
- **正式定义：** 三维度（scope_type × path × op_type）描述操作的影响范围，用于并发调度（`concurrency.py:25-30`）。例如 `read_file("vague_code/main.py")` 的 scope 为 `(filesystem, vague_code/main.py, read)`。
- **常见错误叫法：** Permission Scope——Resource Scope 是为并发调度设计的，不是权限。

### Conflict Serializability（冲突可串行化）

- **一句话通俗解释：** 同时执行多个工具时，保证结果和逐个执行一样。
- **正式定义：** 以 LLM 返回的 tool call 顺序为基准串行序，scope 重叠且至少一方为写者则串行执行，否则并发（`concurrency.py:90-104`）。
- **常见错误叫法：** Dependency Detection——冲突可串行化是数据库理论的标准概念，比依赖检测更严谨。

### 8 个工具（6 基础 + 2 动态）

- **一句话通俗解释：** Agent 与外部世界交互的唯一接口。
- **正式定义：** 6 个基础工具：read_file / write_file / patch / glob / grep / bash（`tools.py:341-348`）；2 个动态注入工具：memory_search（`memory_tool.py:5-19`，memory 开启时注册）、code_search（`tools.py` + `repomap.py`，repo index 成功时注册）。
- **常见错误叫法：** —（没有常见混淆）

### Repo Map（代码库符号地图）

- **一句话通俗解释：** 代码库的"目录索引"——不用 grep 反复搜，直接知道函数/类在哪。
- **正式定义：** 基于 tree-sitter 的符号索引子系统（`repomap.py`）。`Agent.start()` 构建一次，以 `file:line: signature` 列表注入 system prompt（`max_map_tokens=1000` 硬上限），并注册 `code_search` 工具按需查询。热度按符号被引用次数排序（近似 Aider 图排序）。详见 ADR-0016。
- **常见错误叫法：** Vector Index、Code Search DB——vague-code 用 tree-sitter 本地解析，不是向量检索。

---

## 分组 3：压缩层

### Token Budget（预算）

- **一句话通俗解释：** 上下文窗口的"费用上限"——最多能用多少 token。
- **正式定义：** 模型 context_window × 0.9（`context_tokens.py:131-136`），预留安全余量。
- **常见错误叫法：** Context Limit——Budget 强调的是主动预算管理，Limit 是硬上限。

### stale_snip

- **一句话通俗解释：** 删掉那些被后续读取"覆盖"了的旧文件内容。
- **正式定义：** 压缩流水线第一层。纯规则驱动，零 LLM 成本。扫描消息流，删除被后续同路径读取操作覆盖的旧 `tool_result`（`context_compress.py:69-136`）。

### microcompact

- **一句话通俗解释：** 把超长工具输出折叠为"头+尾"摘要，保留原文指针。
- **正式定义：** 压缩流水线第二层。head+tail 结构化折叠，保留原文位置指针供必要时回溯（`context_compress.py:151-208`）。

### auto_compact

- **一句话通俗解释：** 用 LLM 自己总结历史对话，节省空间。
- **正式定义：** 压缩流水线第三层。上下文利用率 > 85% 时触发，对历史消息做 LLM 驱动的摘要（`context_compress.py:221-351`）。

### truncation（截断）

- **一句话通俗解释：** 硬截断——最后的兜底方案。
- **正式定义：** 压缩流水线第四层。保留 system prompt + task 描述 + 最近 N 轮消息，丢弃中间内容（`context_compress.py:356-484`）。

### KV Cache

- **一句话通俗解释：** LLM 逐 token 推理时，前文算过的注意力结果不重算——只算增量。
- **正式定义：** Transformer 自回归推理的注意力 Key/Value 张量缓存。每生成一个 token，该 token 的 Key 和 Value 张量被保存到缓存；后续 token 只需计算与已缓存 K/V 的增量注意力，将每步 O(n²) 降为 O(n)。这是单次请求内部的底层推理优化，对 API 调用者透明。

### Prompt Caching

- **一句话通俗解释：** 多次请求之间，前缀重复的部分只编码一次——省 token 省延迟。
- **正式定义：** LLM 提供商基于底层 KV Cache 的跨请求缓存复用优化。服务端持久化请求前缀的 K/V 张量，新请求前缀匹配时跳过重复编码直接复用缓存。Anthropic 通过 `cache_control` 字段显式标记断点位置（`codecs/anthropic.py`），DeepSeek/OpenAI 自动检测连续请求的前缀匹配。vague-code 三段式 system prompt 将不变的 identity 段置于消息首部，最大化跨请求的缓存命中率（ADR-0007）。

---

## 分组 4：权限层

### Permission Mode（模式）

- **一句话通俗解释：** Agent 的安全等级——从"什么都问"到"完全信任"。
- **正式定义：** safe / normal / autoedit / auto 四种模式，按操作可逆性切分信任等级（`permission.py:14-18`）。
- **常见错误叫法：** Security Level——Permission Mode 更强调操作权限视角。

### Decision（决策）

- **一句话通俗解释：** 权限判定的结果——同意、确认还是拒绝。
- **正式定义：** ALLOW（放行）/ CONFIRM（需人工确认）/ DENY（直接拒绝），三种枚举值（`permission.py:8-11`）。

### Audit Log（审计日志）

- **一句话通俗解释：** 每次权限决策都记了一笔，随时可查。
- **正式定义：** 每次决策生成一条 `permission_check` 事件，写入事件流（`loop.py:519-522`）。

### 三层规则体系

- **一句话通俗解释：** 全局规则 → 会话规则 → 单次豁免，层层覆盖，DENY 最高。
- **正式定义：** 持久规则（配置文件）→ 会话级规则（运行时添加）→ 单次豁免（Y/✓），DENY 优先级最高（`permission.py:33-37`）。

---

## 分组 5：记忆层

### Pinned Memory（常驻记忆）[已废弃]

- **一句话通俗解释：** 始终注入到系统提示的知识，像写在脑子里的便利贴。
- **正式定义：** 曾用于偏好/约定类信息，始终附加到 system prompt，每次 turn 都可见。**已移除**（ADR-0016 配套决策）——其"常驻知识"职责由 `.agent/rules.md` 层级加载（ADR-0008）承担。
- **历史代码位置：** `loop.py:202-208`（已删除）

### Episodic Memory（情景记忆）

- **一句话通俗解释：** 按需检索的历史经验，像翻笔记本找上次的写法。
- **正式定义：** 通过 `memory_search` 工具按 LIKE 查询 + 热度排序检索（`memory_tool.py:5-19`）。

### Distillation（蒸馏）

- **一句话通俗解释：** 自动从对话中提炼有价值的信息存入记忆。
- **正式定义：** auto_compact 摘要作为新的 episodic 记忆增量入库，SHA-256 去重（`loop.py:323-329`）。

---

## 分组 6：模型层

### IR（Internal Representation，内部表示）

- **一句话通俗解释：** 统一的消息内部格式，不管用什么厂商的模型。
- **正式定义：** 自定义 dataclass，语义照抄 Anthropic content block 模型。四种 Block 类型：text / thinking / tool_use / tool_result（`ir.py:8-83`）。上层业务代码不见 Codec。
- **常见错误叫法：** Message Format——IR 不仅包含消息，还包括 StopReason、NormalizedUsage、StreamEvent 等。

### Codec（编解码器）

- **一句话通俗解释：** IR 与厂商接口格式之间的翻译器。
- **正式定义：** 每个 LLM 后端一个薄翻译层：DeepSeek codec（`codecs/deepseek.py`）、Anthropic codec（`codecs/anthropic.py`）。负责 tool_call id 映射、角色交替修复、usage 归一化。上层代码零分支。

### Block（内容块）

- **一句话通俗解释：** 消息的最小内容单元——一段文字、一个工具调用、一个思考过程。
- **正式定义：** TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock 四种类型（`ir.py:8-83`），可在同一消息中交织排列。

### StreamEvent（流事件）

- **一句话通俗解释：** 流式输出的统一事件类型——消息开始、文本增量、工具调用等。
- **正式定义：** 10 种事件类型（`ir.py:255`），含 MessageStart、TextDelta、ToolUseStart、ContentBlockStop 等。所有厂商的流式输出统一映射到这套事件上。

---

## 分组 7：评测层

### Ablation Experiment（消融实验）

- **一句话通俗解释：** 开关某个特性看看效果——控制变量法。
- **正式定义：** 固定其他变量，开关单一特性（压缩/记忆/并发），对比 pass rate、token 消耗、正确率等因变量。

### Fail-to-Pass

- **一句话通俗解释：** 修 Bug 后，原来挂的测试必须通过——证明 bug 被修好了。
- **正式定义：** SWE-bench 验收测试双清单之一。修 bug 任务必须使 fail-to-pass 测试从挂到过。

### Pass-to-Pass

- **一句话通俗解释：** 修 Bug 后，原来过的测试不能挂——证明没引入新 bug。
- **正式定义：** SWE-bench 验收测试双清单之一。修 bug 任务必须保证 pass-to-pass 测试不回归。Fail-to-Pass + Pass-to-Pass 两者共同构成"修了一个 bug 没引入新的 bug"的证据。

### FakeBackend

- **一句话通俗解释：** 模拟 LLM 后端，跑测试不用花钱调 API。
- **正式定义：** 始终返回 `TextBlock("ok")` + `stop_reason=end_turn` 的模拟后端（`harness.py:129-141`），用于零 API 成本验证评估框架和压缩/记忆等内部功能。

---

## 下一篇

→ **02-architecture-overview.md**：把 10 个子系统串起来，理解它们如何协作。

**相关链接：** CONTEXT.md（机器可读术语规范）

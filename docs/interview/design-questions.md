# 面试设计题分析手册

> 面向 vague-code 项目的面试设计题准备文档。每个主题按"面试问题 → 分析思路 → 回答要点 → 加分深度 → 数据与坑"五段式组织。
> 用途：架构设计题 + 简历深挖。
> 数据来源：docs/adr/（18 份）、docs/Coding Agent 项目开发文档.md、docs/faq.md、eval/results.md、docs/known-issues.md、源码。

---

## 0. 总叙事：60 秒简历故事

**项目一句话**：面向真实编码场景的轻量级本地 Coding Agent CLI——自研 Agent Runtime、工具系统、五层上下文压缩、冲突可串行化并发调度、权限安全体系、跨会话记忆，以及配套自动化评测工具链。

**叙事弧线**（从"为什么做"到"怎么证明"）：
1. 使用 Claude Code 时发现三个真实痛点：**上下文膨胀**（长会话丢目标）、**工具误调用**（模型瞎调工具浪费轮次）、**危险操作不可控**（rm -rf 无人知晓）。
2. 结论是"要验证自己的想法，必须自己写一个"——不是抄，是复刻架构 + 自研机制 + 评测验证。
3. 交付：30 题 SWE-bench 评测，基线 pass rate 60%（18/30）；消融实验证明并发调度的收益 **+33pp（93%）** 且 token 消耗最低（614K，-34%）。
4. 全栈自研：自定义 IR + 双厂商 codec、event-sourced 轨迹、五层压缩、冲突可串行化、纯函数权限决策、SQLite 记忆、tree-sitter repo map、Textual TUI。

**岗位映射**（可以主动提）：DeepSeek Harness / Kimi 基础设施 / 阿里腾讯 Agent 应用 / 字节美团 AI 平台——每类 JD 都对应到本项目的一个子系统（评测链 / 模型抽象 / 上下文工程 / 工具与并发）。

---

## 1. 库优先架构（ADR-0001）

**面试问题**：为什么 Agent 设计成库而不是 CLI？你的 CLI 那么薄，意义在哪？

**分析思路**：核心约束是**评测 harness 必须编程控制 Agent**——它要开关自变量（压缩/并发/repo_map）、采集内部指标（压缩回收、token usage）。如果 Agent 是 CLI，只能通过 subprocess 调用，指标采集脆、进程开销高。反过来想：什么场景需要"无 UI 的 Agent"？答案就是评测和自动化。

**回答要点**：
1. 核心接口是 `Agent(config).run(task, workdir) → Trajectory`——一个纯 Python 编程接口。
2. CLI/TUI/Eval Harness 全部是它的"消费者"（三种 RunHandle 用法：迭代器给 CLI/TUI 渲染，`run()` 一键给 Eval）。
3. 所有模块只通过 `AgentConfig` 暴露配置，不依赖 CLI 全局状态。
4. 副作用隔离：评测每任务独立 worktree。

**加分深度**：这个设计带来一个"红利"——**subagent 委派 = 嵌套调用 `Agent.run()`**（plan 0015 的第一性原理）。因为 Agent 是库，子 Agent 只是再 new 一个实例跑一次 run()，不需要任何进程/接口层面的新基建。架构决策的"复利"是面试高分层。

**数据与坑**：270+ 次运行的评测（30 题 × 配置 × 重复 = 720 次 run），如果 subprocess 黑盒会带来不可接受的进程开销和指标采集脆弱性。**坑**：不要说"CLI 简单所以我做了个库"，要强调"评测是库化的第一驱动力"。

---

## 2. 模型抽象层（ADR-0002 / ADR-0005）

**面试问题**：为什么自定义 IR 而不直接用厂商 SDK 类型？为什么每厂商一个 codec？

**分析思路**：约束是**上层需要厂商协议里不存在的元数据**——stale 标记、折叠状态、cache_control 断点、token 估算、event id。厂商 SDK 类型装不下这些。其次，Anthropic 的 block 模型是超集（text/thinking/tool_use/tool_result 交织在同一 message），OpenAI 协议做不到，需要中间层做投影。

**回答要点**：
1. 自定义 dataclass IR（语义照抄 Anthropic content block 模型）：TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock 四种 block 交织，携带 `meta` 内部元数据。
2. 每厂商一个**薄 codec**（200-400 行），只做 IR ↔ wire format 双向转换，上层业务代码零厂商分支。
3. 新增后端 = 新增一个 codec 文件，核心零改动（用 Anthropic codec 验证过这个承诺）。
4. 流式统一为 10 种 StreamEvent（MessageStart → ... → MessageEnd + RetryNotice），codec 纯翻译，Loop 统一消费。

**加分深度**：两个设计细节——① 流式工具参数是**增量**（ArgsDelta 多段拼接），因为 SSE 可能任意字符处断（真实 golden transcript 里 `"{\"path\": "` 和 `"\"README.md\"}"` 分两段）；② golden transcript 快照测试：真实 API 响应存档，decode 后 `to_dict()` 比对，厂商格式一变测试就炸——这是"厂商格式变更第一时间告警"的机制。

**数据与坑**：Anthropic codec 因为 IR 语义照抄它的 block 模型所以是"直通映射"，DeepSeek 才是"翻译"（reasoning_content → ThinkingBlock）。**坑**：别说"我用 SDK 就行"，面试官会追问元数据（stale/折叠指针）厂商协议里没有。

---

## 3. 轨迹系统（ADR-0003）

**面试问题**：为什么轨迹用 event-sourced JSONL → SQLite，而不是只存 messages 数组？

**分析思路**：先想 messages 数组丢了什么——**重试记录、压缩事件、权限决策、耗时**，这些消融实验必须的信息在 messages 里天然不存在。其次，"先有完整事实，再选视角导出"的顺序不可逆：如果只存 messages，以后想重算（judge 改版、失败重分类）就没素材了。

**回答要点**：
1. 事件流 12 种 EventType（run_start/turn_start/compression/llm_response/tool_call/permission_check/...），每行带类型、时间戳、token usage、延迟。
2. 存储：JSONL 不可变 + SQLite 可查询（runs 表 + events 表），双存储各司其职。
3. messages 只经 `to_messages()` 导出，供 LLM-as-Judge 消费。
4. 否决过两个方案：双写（一致性复杂）、纯 messages（有损）。
5. schema 演化走 payload JSON 加字段，向后兼容。

**加分深度**：`to_messages()` 的细节——run_start 拆 system+user、llm_response 前 flush pending tool results、`seen_tool_result_ids` 去重防 resume 重复。事件流是评测与 Agent 之间**唯一数据契约**。

**数据与坑**：resume 会重复 compression 事件（known-issues U2），分析侧按 `(run_id, turn, layer)` 去重。**坑**：别把轨迹说成"日志"，它是事件溯源，是可重放、可重算的数据源。

---

## 4. 工具系统 + 并发（ADR-0004 / ADR-0012）

**面试问题**：工具为什么抽象成 `Tool{spec, factory}`？工具并发为什么用"冲突可串行化"而不是依赖图或直接并行？

**分析思路**：工具是 Agent 与世界的唯一接口，必须可控。约束有两个：① 工具逻辑不能内嵌 loop（否则 if 分支爆炸）；② **并发只能加速，不许改变可观察世界**——这是并发调度的底线。模型输出多个工具调用时串行执行浪费，但乱序执行可能改错世界，所以需要一个"保证等价于串行"的并发模型。

**回答要点**：
1. `Tool(spec, factory)`：spec 是 JSON Schema 声明，factory 在 `bind(workdir)` 时产出闭包 handler `(input) → str`；注册约束 `registry key == spec.name`。
2. 冲突可串行化：LLM 输出顺序 = 基准串行序；并发执行必须在可观察效果上**等价**于串行。
3. scope 模型 `(path, EXACT/PREFIX/WORKSPACE, R/W)`：双读不冲突；任一 WORKSPACE 必冲突；同 path 冲突；PREFIX 覆盖冲突。
4. bash 保守判 WORKSPACE（全串行）——因为它可能碰任何东西。
5. 贪心分组，组内并发（ThreadPoolExecutor ≤4）、组间串行；失败在组边界传播（下游 skipped）。

**加分深度**：并发结果**按原调用顺序组装**回喂（`result_by_id` + 按 calls 顺序），因为 LLM 按它发出 tool_use 的顺序理解结果——世界可以被并发改变，但 LLM 看到的"故事线"必须串行。这是冲突可串行化的落地。

**数据与坑**：消融实验里**并发是最大单项收益**：OFF 83%/635K → ON 93%/614K（+10pp、-3% tokens）。**坑**：别说"直接 ThreadPool 全并行"，面试官会追问"如果两个工具同时写同一个文件呢"——答案就是冲突检测 + 组间串行。

---

## 5. 上下文工程（ADR-0011 / ADR-0017 / ADR-0009）

**面试问题**：为什么压缩要五层？为什么层序是"从精准回收到盲目切割"？压缩为什么不落盘？

**分析思路**：核心约束是长会话（30+ 轮）token 必然爆，而不同层的**回收精准度不同、成本不同**。stale_snip 零损失零成本，truncation 盲目切割兜底。正确顺序是"最精准的先做，最盲目的最后做"。

**回答要点**：
1. 五层流水线：**stale_snip**（每轮无条件，同 (tool,path) 旧读取换占位符，零损失）→ **microcompact**（util>0.5，超长输出 head20+tail10 折叠）→ **structured_snip**（util>0.65，识别"读→改→测"闭合子任务替换为结构化摘要，零 LLM 成本）→ **auto_compact**（util>0.85，LLM 摘要旧轮次，最贵最准）→ **truncate**（兜底，锁 system+首条 user，tool_use/tool_result 原子成对）。
2. 任何层不删消息、不破坏 tool_use/tool_result 配对，只替换 block content。
3. 每层纯函数 `messages → messages`，产出 LayerReport 落 compression 事件（可观测）。
4. **压缩不落盘**：resume 重新压缩，避免持久层一致性风险（但导致 U2 重复事件）。

**加分深度**：structured_snip 是五层里最新、最"算法"的一层——从轨迹事件流识别闭合子任务（work_start 开 → 成功 bash 闭 → 失败 bash 不断裂），零推理成本用掉轨迹里已结构化的信息。auto_compact 的摘要同时蒸馏进记忆库（`loop.py:340-347`）——压缩和记忆是联动的。

**数据与坑**：消融实验的**反直觉结论**：压缩在短会话（<30 轮）是**负收益**（76% vs 基线 83%），因为 auto_compact 的 LLM 调用成本 > 回收收益。压缩是为 30+ 轮长会话设计的。**坑**：别吹"压缩省了 30% token"而忽视它在短会话的负收益——主动讲出这个边界才是加分项。

---

## 6. 权限系统（ADR-0013）

**面试问题**：权限决策为什么是纯函数？四种模式怎么划分的？CONFIRM 在 CLI 下会怎样？

**分析思路**：安全系统的核心需求是**决策可审计、可测试、可重放**——每个决策必须能拿同样的输入重算验证。纯函数 `evaluate(mode, operation, rules) → Decision` 天然满足。

**回答要点**：
1. `evaluate()` 是纯函数：同输入同输出，无共享状态，线程安全。
2. 4 种模式按**操作可逆性**分档：safe（只读）→ normal（写需确认）→ autoedit（写自动、命令确认）→ auto（危险命令仍确认）。
3. 三层规则：持久（`.agent/permission-rules.json`）/ 会话（内存）/ 单次豁免；规则匹配优先于模式策略。
4. 24 类危险命令正则；bash 分类**先危险后安全、未知默认危险**（fail-closed）。
5. 每次决策落 `permission_check` 审计事件。

**加分深度**：两个反直觉点——① **规则之间不是"DENY 最高"，而是"first-match-wins"**（顺序匹配命中即返回，`permission.py:142-146`），"DENY 最高优先级"准确含义是"规则（含 DENY）压过模式默认策略"；② **CLI 下 CONFIRM 自动降级 DENY**（`loop.py:548-557`，无回调则拒绝），TUI 才有弹窗。

**数据与坑**：被拒的工具也必须回喂 `ToolResultBlock(is_error=True)`——为了**配对不变式**：每个 tool_use 必须有对应 tool_result，否则 assistant 消息悬空、LLM 下一轮看不到"这条路走不通"。**坑**：面试官问"拒绝的工具为什么不直接跳过"，别答"自然"，要答"配对不变式 + LLM 需要反馈"。

---

## 7. 记忆系统（ADR-0014）

**面试问题**：跨会话记忆为什么用 SQLite + LIKE 而不是向量库？为什么移除 pinned？

**分析思路**：先界定边界——**记忆管跨会话，上下文管当前对话**。约束是数据量级（1000-10000 条）和零外部服务（不能依赖向量服务）。移除 pinned 的原因是它和 `.agent/rules.md` 功能重复且更弱。

**回答要点**：
1. SQLite 单文件统一记忆库，只保留 `kind='episodic'`（按需检索）。
2. 检索 v1 用 LIKE 分词 + 热度排序 `use_count×100/minutes_since_last_use`；v2 可换 FTS5/BM25 或向量，只改 search 方法。
3. 写入走 auto_compact 蒸馏（`loop.py:340-347`），`content_hash`(sha256) 幂等去重。
4. pinned 常驻注入被判伪需求移除：全局 memory.db 无项目隔离，且 `.agent/rules.md` 层级加载（ADR-0008）可完整替代。

**加分深度**：记忆系统**无项目隔离是已知缺陷**——memories 表无 project 字段，跨项目会污染。标准诚实答法：主动承认 → 根因（与 pinned 同源）→ 修复方向（方案 A 加 project 字段 / 方案 B memory_db_path 跟随 workdir）→ 业界双轨（项目约定走 rules.md，全局偏好走记忆）。

**数据与坑**：评测中记忆**关闭**（`harness.py`），不在消融因变量里——主动说这个更可信。**坑**：别吹"记忆提升了 X%"，当前记忆没进消融矩阵，说了会被戳穿。

---

## 8. Repo Map（ADR-0016）

**面试问题**：代码理解为什么用 tree-sitter 而不是 embedding/向量索引？

**分析思路**：约束是**零外部服务**——embedding 需要外部模型服务，违反项目铁律。而 SWE-bench 评测里大量轮次浪费在 grep 定位符号，需要的是"符号在哪"，不是"语义相似"。

**回答要点**：
1. tree-sitter 本地解析建符号索引（Symbol / RepoIndex），v1 仅 Python（评测 30 题全是 Python）。
2. 双通道：`code_search` 工具（按需查询，scope EXACT/READ）+ system prompt 注入 top-N 符号地图（`max_map_tokens=1000` 硬上限）。
3. 热度排序用符号引用次数近似 Aider 的 graph ranking。
4. `Agent.start()` 构建一次 + mtime 增量刷新；构建失败降级不注入，不影响主循环。

**加分深度**：repo_map 是评测矩阵的**第三维自变量**（2×2×2）。注入地图 token 上限 1000 是"不可被压缩回收"的硬约束——别超，否则侵占压缩预算。

**数据与坑**：tree-sitter-python 最新只有 0.25.0，锁定 `tree-sitter==0.26.0 + tree-sitter-python==0.25.0`（计划文档里 >=0.26.0 是笔误）。**坑**：别答应面试官"支持多语言"，诚实说"v1 只做了 Python，多语言是自然扩展"。

---

## 9. TUI（ADR-0015 + ADR-0019 v2）

**面试问题**：为什么用 Textual？核心循环是同步的，TUI 怎么跟 UI 线程配合？v2 重写改了什么？

**分析思路**：TUI 需要全屏交互（流式输出、权限弹窗、会话管理），Textual 与 Rich 同生态、原生支持 ModalScreen。难点是**核心零 asyncio**（Agent 同步循环）与 Textual 的 asyncio 事件循环如何桥接。

**回答要点**：
1. 选 Textual：Rich 同生态、ModalScreen、线程安全原语完善。
2. Agent 在后台线程跑（`run_worker(thread=True)` → `VagueCodeAgentRunner`），Textual 主线程跑 asyncio 循环；事件经 `call_from_thread` 回主循环；权限弹窗用 `run_coroutine_threadsafe` + `push_screen_wait` 阻塞回传。
3. 回调钩子（`_on_permission` / `on_tool_result` / `on_state_change` / `guidance_provider`）让 TUI 不入侵核心循环——核心不认识 TUI。
4. v2 重写（ADR-0019）：UI 层移植 firstcoder 参考包的分层架构——`TuiTranscript` 单一事实源、views 纯函数渲染、`CompositeCommandHandler` 命令路由、流式 Markdown 三层缓冲（0.2s flush + update guard + 流式禁选）、活动动画与回合 metrics、picker、prewrite diff 写入审查 + 拒绝反馈闭环；侧边栏移除改 `/resume` picker；turn token 过期过滤防事件污染。
5. 代价：+依赖、线程桥接点、~200ms 冷启动。

**加分深度**：核心层复用 `dispatch_event` + StreamEvent IR；CLI 用 `RichStreamVisitor`，TUI v2 已弃用 visitor——事件经 `VagueCodeAgentRunner` 回调直达 transcript。Eval Harness 完全不设回调——同一核心，三种消费者。agent 层为 TUI 做了 4 处小改（`on_tool_result` 带 tool id、`Operation.review/feedback`、`guidance_provider`）。

**数据与坑**：权限弹窗 120 秒超时，超时降级 DENY。**坑**：别说"Agent 是异步的"，核心是**零 asyncio 同步循环**，异步只在 TUI 层；也不要提已删除的 `visitor.py` 侧边栏——v2 已换成 transcript 驱动。

---

## 10. 重试 / Checkpoint / Resume（ADR-0006）

**面试问题**：重试为什么分两层？checkpoint 放在哪？resume 怎么保证幂等？

**分析思路**：LLM 调用链故障点多（网络、限流、服务器），需要韧性；但重试必须可控、可审计、可消融。checkpoint 的本质是"世界副作用的落盘点"——工具执行改变了世界，必须在它之后落盘。

**回答要点**：
1. 两层重试：SDK 层 `max_retries=2` 吸收瞬时抖动；Loop 层指数退避 + 全抖动做策略级重试（`uniform(0, min(max, base×2^i))`）。
2. 错误分类：超时/连接/限流/服务器/流断开可重试；认证/bad request/codec 错误不可重试。
3. checkpoint 两处：LLM 响应后（存"LLM 说了什么"）+ 工具执行后（存"世界变成了什么"）。
4. resume 六步：幂等检查（有 run_end 直接返回）→ 一致性校验 → from_db → to_messages → 末条 stop_reason 分析 → 回放 pending 工具。
5. 回放时 `check_confirm=False`：轨迹已记录当时的权限决策，resume 不能重弹窗（无人值守 + 结果必须与首次一致）。

**加分深度**：重试**不消耗 turn**（内层 while 在 llm_response 之前）；流中断重试丢弃半截响应从头来（LLM 非确定性）；每次重试新建 `_StreamAggregator` 清空半截 buffer。工具执行是 **at-least-once 语义**（崩溃重做已执行工具，v0 接受）。

**数据与坑**：persist 失败三层兜底——主写库 → 退 JSONL recovery 文件 → 补 run_end 保终态。**坑**：第三层"补 run_end"防的是内存对象悬空，**不保证持久化**——诚实说"尽力而为"，别吹成强保证。

---

## 11. 评测 harness（主设计文档 5.6）

**面试问题**：评测系统为什么是"控制变量的 test harness"而不是离线日志分析器？

**分析思路**：要证明"并发/压缩/记忆"这些设计决策的价值，必须**固定其他变量、只开关单一特性**对比。离线分析日志只能看到结果，不能控制自变量。所以评测必须**编程驱动 Agent**（这正是库优先的由来）。

**回答要点**：
1. 控制自变量：AgentConfig 开关（compression × concurrency × repo_map，2×2×2×repeat）。
2. 测量因变量：从 Trajectory 提取 13 项统计（pass rate、tokens、各层压缩回收、轮次）。
3. 任务一题一目录，SWE-bench 格式：`FAIL_TO_PASS`（修 bug 必须从挂到过）+ `PASS_TO_PASS`（不回归）双清单。
4. 每任务独立 worktree 隔离副作用；评测中权限开 auto、记忆关闭。
5. `--fake` 用 `_FakeBackend` 零 API 成本验证框架。

**加分深度**：F2P/P2P 是 SWE-bench 验收双清单的核心——修一个 bug 不能引入新 bug。评测轨迹是 event-sourced，支持"失败重分类"不重跑 Agent（离线重算）。

**数据与坑**：全量 30 题 × 8 配置 × 3 重复 = 720 次 `Agent.run()`，约 200M-300M tokens。基线 60%/931K。**坑**：别说"我们测了 720 次很牛"，要强调"这是控制变量的消融，不是跑量"。

---

## 12. 教学新发现：反直觉点（面试高频坑）

这部分是教学中验证的、文档容易误导的**反直觉真相**，面试被追问时是区分度。

**12.1 max_turns 是"硬墙"不是"预算"**
- 续轮唯一途径是 `tool_use`；而最后一轮要工具会被 `loop.py:420` 熔断（`turn+1 >= max_turns`）——直接 run_end(pending=n)，不执行。
- 因此 `turn_box` 永远停在 `max_turns-1`，`loop.py:505` 的 while 兜底 `run_end(max_turns)` 正常流程**不可达**（known-issues U3，防御性死代码）。
- 面试答法："max_turns 不是让你用满预算，而是在第 max_turns-1 轮想调工具时切掉。真正的'耗尽'是被迫熔断，不是正常耗尽。"

**12.2 一次 turn 只新增一对 (assistant, user)**
- 无论 LLM 请求几个工具、几个被拒，`tool_results` 打包成**一条** user 消息（`loop.py:501`）。
- 面试答法："LLM 一次请求 3 个工具，1 个被拒 2 个执行，本轮也只新增 1 对消息——配对是 turn 的边界。"

**12.3 emit vs persist：记账 vs 交账**
- `emit` 造 Event 追加进内存 list（微秒级）；`persist` 批量写 SQLite（毫秒级，WAL + busy_timeout + 增量 `_persisted_count`）。
- 为什么分离：emit 太频繁、persist 太贵，先攒内存后批量落盘。

**12.4 turn 是 0 索引**
- `max_turns=5` 时最后一个能执行工具的 turn 是 3（索引），熔断发生在 turn 4。面试算数题。

---

## 13. Subagent 委派（ADR-0018，proposed）

**面试问题**：单 Agent 有什么瓶颈？为什么 subagent 只是"嵌套 run()"？

**分析思路**：单 Agent 架构下大项目内容全堆同一上下文，工具并发无法并行探索，压缩只是"消化"不是根治。解法是 subagent 并行探索——而库优先（ADR-0001）让"subagent = 嵌套 `Agent.run()`"成为零新基建的方案。

**回答要点**：
1. 只新增桥接工具 `delegate_task`，subagent 就是嵌套的 `Agent.run()`。
2. v1 只读委派（read/glob/grep/code_search/memory_search），防递归（子 Agent 禁 delegate）。
3. 成本双硬上限：`max_turns=8` + `max_subtasks=4`。
4. 血缘：`run_start.parent_run_id` 追溯父子轨迹；主 Agent 轨迹只记一条 delegate 调用 + 摘要结果。
5. 默认关闭；v1 不进评测矩阵（防变量爆炸）；演进 v1 只读 → v2 读写 → v3 自动任务分解。

**加分深度**：这个设计是"架构红利"的典型例子——因为 01 做了库优先，18 的 subagent 才只是加一个工具的事。**坑**：别把它说成"已实现"，它是 proposed 待实施状态，诚实标注。

---

## 14. 已知缺陷的诚实答法

面试遇到"你这个系统有什么缺点"——标准模板：**主动承认 → 根因 → 修复方向**。硬说"设计如此"最不可信。

### 14.1 记忆无项目隔离（最重点，必讲）
- **问题**：memories 表无 project 字段，episodic 记忆全局共享，跨项目污染。
- **根因**：当初判定 pinned 是伪需求时用了"全局无隔离"的理由，但 episodic 留下同样隐患——已知的不一致，不是正确设计。
- **修复方向**：A 加 project 字段（ingest 记 workdir/repo，search 按 project 过滤）；B memory_db_path 跟随 workdir（每项目一个 DB）。
- **业界双轨**：项目约定走 `.agent/rules.md`（ADR-0008，已解决隔离），跨项目偏好走全局记忆。

### 14.2 truncate 尾部贪心时序偏移（U1）
- 重写算法代价高，per-message token 缓存（R2）只缓解性能。诚实说"算法不完美，但触发次数是流水线健康指标"。

### 14.3 resume 重复 compression 事件（U2）
- 分析侧按 `(run_id, turn, layer)` 去重即可，不修代码。

### 14.4 `loop.py:505` 死代码（U3）
- 防御性兜底正常流程不可达，不改代码，文档标记。

### 14.5 工具执行 at-least-once
- 崩溃重做已执行工具可能产生副作用重复，v0 接受，长会话场景可升级为 exactly-once（需事务性工具）。

---

## 附：三类面试题型速查

| 题型 | 问法 | 答题框架 |
|---|---|---|
| 架构设计 | "你会怎么设计一个 coding agent？" | 三个痛点 → 分层（Loop/工具/上下文/权限/记忆）→ 关键取舍（纯函数、事件溯源、冲突可串行化）→ 评测闭环 |
| 简历深挖 | "这个 93% 是怎么来的？" | 消融实验 → 控制变量 → 并发是最大收益 → 基线 60% vs 并发 93% → 压缩短会话负收益的诚实边界 |
| 源码细节 | "loop 怎么终止的？" | 五种终止 + 0 索引 + max_turns 硬墙（420 熔断）+ 505 死代码 + 一次 turn 一对 |

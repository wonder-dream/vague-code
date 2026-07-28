# XClaw 文档编写总纲

本文档为 XClaw 项目的完整文档蓝图。供多轮多次对话中使用。

---

## 目录结构

```
docs/
├── guide/                         ← 学习路径（按编号顺序阅读）
│   ├── 00-what-is-a-coding-agent.md
│   ├── 01-terminology.md
│   ├── 02-architecture-overview.md
│   ├── 03-a-single-turn-explained.md
│   ├── 04-agent-runtime.md
│   ├── 05-tool-system.md
│   ├── 06-context-engineering.md
│   ├── 07-permission-system.md
│   ├── 08-memory-system.md
│   ├── 09-model-abstraction.md
│   ├── 10-trajectory.md
│   ├── 11-cli-and-tui.md
│   └── 12-evaluation-harness.md
│
├── tutorials/                     ← 动手教程
│   ├── 01-your-first-task.md
│   ├── 02-fixing-a-real-bug.md
│   ├── 03-extending-xclaw.md
│   └── 04-running-ablation-experiments.md
│
├── reference/                     ← API 参考（表格风格）
│   ├── agent-config.md
│   ├── ir-reference.md
│   ├── tool-api.md
│   ├── trajectory-events.md
│   └── cli-reference.md
│
├── troubleshooting.md             ← 常见问题与解决方案
├── faq.md                         ← 设计决策问答
├── adr/                           ← 已有（15篇），新增 README.md 索引
├── plans/                         ← 已有（12篇）
├── blog/                          ← 已有（1篇）
├── audit/                         ← 已有（5篇）
├── reviews/                       ← 已有（2篇）
├── devlog.md                      ← 已有
├── known-issues.md                ← 已有
└── Coding Agent 项目开发文档.md    ← 已有

根目录：
├── README.md        ← 已有
├── CONTEXT.md       ← 已有
├── CHANGELOG.md     ← 已有
├── LICENSE          ← 新建（MIT）
└── Agent.md         ← 已有
```

---

## 学习路径（按顺序）

```
Phase 0: 概念入门（零基础可读）
  00 → 01 → 02

Phase 1: 架构全景（系统级理解）
  03

Phase 2: 子系统深潜（逐个模块吃透）
  04 → 05 → 06 → 07 → 08 → 09 → 10 → 11 → 12

Phase 3: 动手教程（学以致用）
  T1 → T2 → T3 → T4

Phase 4: API 参考（按需查阅）
  R1 / R2 / R3 / R4 / R5

Phase 5: 补充（随时查阅）
  故障排查 / FAQ
```

---

## Phase 0 — 概念入门（3 篇）

### 00-what-is-a-coding-agent.md | ~400 行

**定位：** 所有文档的第一篇。不分技术背景都能看懂。

**大纲：**
1. 一句话说清楚 — "你告诉它要做什么，它自己读代码写代码跑测试"
2. 和 Copilot/ChatGPT 的区别 — 自主模式 vs 补全模式 vs 问答模式
3. 一次完整任务的旅程（图解）— 从 "修一下 mean()" 到验证完成
4. Agent 的能力清单 — 读/写/搜索/运行命令/修 Bug/添功能/重构
5. XClaw 的特别之处 — 压缩 / 权限 / 记忆 / 并发
6. 你需要准备什么 — Python 3.12 / API Key / 项目
7. 下一篇：→ 01-术语表

**核心素材：** 无（概念文档）

---

### 01-terminology.md | ~300 行

**定位：** 随时回来查的术语字典。**分组排列，先通俗后精准。**

每个术语格式：① 一句话通俗解释 ② 正式定义 ③ 常见错误叫法

**分组（7 组）：**
| 组别 | 术语 |
|------|------|
| Agent 层 | Agent Runtime / Turn / Trajectory / ReAct Loop / Checkpoint |
| 工具层 | Tool Spec / Resource Scope / Conflict Serializability |
| 压缩层 | Token Budget / stale_snip / microcompact / auto_compact / truncation / KV Cache |
| 权限层 | Permission Mode / Decision / Audit Log |
| 记忆层 | Pinned Memory / Episodic Memory / BM25 / Distillation |
| 模型层 | IR / Codec / Block / Stream Event |
| 评测层 | Ablation Experiment / Fail-to-Pass / Pass-to-Pass |

**核心素材：** `CONTEXT.md`（机器可读术语规范）

---

### 02-architecture-overview.md | ~500 行（含 3 张 ASCII 图）

**定位：** 把 10 个子系统串起来，理解它们如何协作。

**大纲：**
1. 一张图看懂 — 分层架构（标注数据流箭头）
2. 跟踪一次请求 — "fix the bug" 走完每一层的完整路径
3. 数据住在哪里 — messages/events/memories/rules/config 的存储位置与生命周期
4. 目录地图 — `src/` 每个文件的职责（一张表）
5. 关键不变量 — tool_use/tool_result 成对 / 压缩纯函数 / 权限纯函数 / 轨迹追加 / Agent 零 asyncio

**核心素材：** `README.md`（已有架构图），`src/` 目录结构

---

## Phase 1 — 架构全景（1 篇）

### 03-a-single-turn-explained.md | ~600 行（含标注调用图）

**定位：** 把一次 Agent 循环展开到每个函数调用、每个数据变换。看完就能读懂 `loop.py`。

**大纲（10 个步骤，每个步骤包含：代码位置、输入、输出、数据变换）：**
1. 前情提要 — messages 数组、turn_box、config 的初始状态
2. Build System Prompt — `SystemPrompt.build()` + 规则加载 + pinned memory 注入
3. Compression Pipeline — 4 层压缩，每层读什么改什么
4. LLM Call — `backend.stream()` → SSE chunks → `_StreamAggregator` → `ModelResponse`
5. Parse Stop Reason — end_turn / max_tokens / tool_use 的分支
6. Permission Check — `_check_tool_permission()` 对每个 tool_use 做 DENY/CONFIRM/ALLOW
7. Tool Execution — `execute_concurrent()` 或串行 → ResourceScope → 分组 → ThreadPool
8. Checkpoint — persist trajectory 到 SQLite
9. Next Turn — turn_box[0] += 1，回到 while
10. 完整调用图 ASCII art

**核心素材：** `loop.py:244-495`（_run_gen 核心），`concurrency.py:142-200`（execute_concurrent）

---

## Phase 2 — 子系统深潜（9 篇，每篇 300-600 行）

每篇格式：概述 → 核心概念 → 代码走读 → 配置/边界 → 相关文件 → 上一篇/下一篇

### 04-agent-runtime.md | ~500 行
- 库优先设计（`Agent(config).run() → Trajectory`）
- Agent 类结构（__init__ / start / run / _run_gen / resume）
- ReAct 循环详解
- RunHandle 模式（迭代器 + .trajectory + 上下文管理器）
- _StreamAggregator 工作原理
- 重试系统（RetryPolicy + classify_llm_error + 指数退避）
- Checkpoint/Resume（两个 persist 点 + 恢复流程）
- 已知陷阱（U2 compression 重复事件）
- **引用：** ADR-0001, ADR-0006, plans/0002, plans/0003

### 05-tool-system.md | ~550 行
- 工具哲学 — Agent 的"手"和"眼"
- 6 个工具一览表（name / 作用 / 参数 / 返回值）
- Tool dataclass + bind(workdir) 工厂模式
- 每个工具的深层剖析（路径安全 / 截断 / 边界情况）
- 安全性保证（路径穿越防护 / null 字节 / 50K 截断）
- 并发模型（核心）：
  - ResourceScope 三维度（scope_type × path × op_type）
  - 每个工具的 scope 提取
  - schedule() 分组算法
  - 失败传播与 [skipped] 语义
  - 消融数据（并发 on/off = +10pp pass rate）
- 添加新工具：5 步攻略
- **引用：** ADR-0004, ADR-0012, plans/0010

### 06-context-engineering.md | ~600 行
- 难题：上下文窗口是稀有资源
- 架构：3 个文件 + 1 个模块的职责
- System Prompt 构造（3 段式 + KV Cache 优化）
- 规则文件层级加载（向上遍历 + 10KB 限制）
- Token Budget 计算（模型 context_window × 0.9）
- 4 层压缩流水线（每层详解）：
  - stale_snip：确定性，零 LLM 成本，只标记同工具同路径
  - microcompact：结构化 head+tail 摘要，字符级回退（PR-2 修复）
  - auto_compact：LLM 驱动，keep_turns 保留最近轮次
  - truncation：硬截断，保留 system+task 前缀
- 纯函数设计 + LayerReport + EventType.compression 可观测性
- 消融数据讨论（压缩在小任务中负收益，目标 30+ 轮会话）
- **引用：** ADR-0011, plans/0008, blog/compression.md

### 07-permission-system.md | ~400 行
- 设计哲学：默认安全，渐进信任
- 4 种模式详解（信任等级逐级提升）
- 危险命令分类（18 安全 + 24 危险模式）
- 三层规则体系（全局→会话→单次豁免，DENY 最高优先级）
- 决策函数：纯函数 `(mode, rules, operation) → Decision`
- 审计日志：每次决策一条 `permission_check` 事件
- TUI 交互式确认：Y/✓/Ctrl+Y 持久化
- 空 pattern 防护（B13 修复）
- cp/mv 安全分类（B14 修复）
- **引用：** ADR-0013, plans/0011

### 08-memory-system.md | ~350 行
- 边界：记忆 vs 上下文的设计职责分离
- 存储模型：SQLite + FTS5 表结构
- Pinned 注入：始终附加到 system prompt，用于偏好/约定
- Episodic 检索：`memory_search` 工具，LIKE/BET/FS5 查询
- 写入管道：auto_compact 蒸馏 → 增量 ingest → SHA-256 去重
- 评分：`(use_count × 100) / MAX(1, minutes_since_use + 1)`
- **引用：** ADR-0014, plans/0012

### 09-model-abstraction.md | ~500 行
- 为什么自定义 IR（仿 Anthropic content block）
- Block 类型：TextBlock / ThinkingBlock / ToolUseBlock / ToolResultBlock
- Message / ModelResponse / StopReason / NormalizedUsage
- StreamEvent 10 种类型及层次结构
- Codec 架构：每厂商一个薄翻译器（encode_request ↔ decode_response）
- DeepSeek codec：OpenAI 兼容，reasoning_content 处理，tool_call id 映射
- Anthropic codec：角色交替修正，thinking 块保留，prompt caching
- 添加新提供商：5 步攻略
- Golden transcript 测试：记录 SSE chunk → 解码 → 比对各 IR
- **引用：** ADR-0002, ADR-0005, plans/0001, plans/0005, plans/0006

### 10-trajectory.md | ~300 行
- 事件溯源 vs 状态存储：为什么 JSONL 事件而非消息数组
- EventType 枚举：12 种事件及触发条件
- SQLite 存储：runs 表 + events 表结构
- to_messages()：事件流 → LLM 可消费的消息数组
- from_db() + Agent.resume()：崩溃恢复
- 查询模式：聚合统计 / 过滤事件 / JSONL 导出
- **引用：** ADR-0003, plans/0002-section

### 11-cli-and-tui.md | ~400 行
- 两个界面共用同一个 Agent 库（thin shell 原则）
- Part A: CLI
  - 入口点 xcode / `src/cli/__init__.py`
  - RichStreamVisitor → Rich Console 渲染
  - 参数 / flag / 退出码
- Part B: TUI
  - 架构：Agent 在 @work(thread=True) 中同步运行
  - 4 个布局区域：Conversation / Sidebar / StatusBar / CommandInput
  - TextualStreamVisitor → ConversationView 渲染
  - 权限对话框：ModalScreen + push_screen_wait
  - 会话侧边栏：列表 → SessionDetail → resume
  - 键绑定参考表
  - 斜杠命令参考表
- **引用：** ADR-0015

### 12-evaluation-harness.md | ~450 行
- 架构：Agent 即库 — programmatic control
- SWE-bench 任务格式（tasks.json）
- 评测循环：harness.py → Agent.run() → verify → pass/fail
- 实验矩阵：2×2 (compression × concurrency) × repeat
- FakeBackend：零 API 成本验证
- 报告生成：Markdown + 汇总表
- 当前结果：baseline 60% / 并发 ON 93%
- 添加新任务：攻略
- **引用：** eval/README.md, eval/results.md

---

## Phase 3 — 动手教程（4 篇）

### T1: 01-your-first-task.md | ~300 行
- 安装 → API Key → `xcode "list files"` → 读输出 → 读 JSONL 轨迹 → 实验不同参数

### T2: 02-fixing-a-real-bug.md | ~400 行
- tests/_target_bug/stats.py 的除零 bug
- 全程观察 Agent 修：grep → read → patch → test → end_turn
- 对比不同权限模式的效果

### T3: 03-extending-xclaw.md | ~500 行
- 示例 1：添加 web_search 工具（spec → factory → handler → DEFAULT_TOOLS → 测试）
- 示例 2：添加 Gemini codec（新建 codec/gemini.py → backend.py → golden transcript）
- 示例 3：添加评测任务（task.toml → verify.sh → tasks.json）

### T4: 04-running-ablation-experiments.md | ~350 行
- 矩阵配置 → FakeBackend 验证 → 真实 API 运行 → 报告解读 → 常见陷阱

---

## Phase 4 — API 参考（5 篇，每篇表格风格）

| 篇目 | 内容格式 |
|------|---------|
| R1: agent-config.md | AgentConfig / TransportConfig / CompressionConfig / MemoryConfig 每个字段的表（名称·类型·默认值·约束·示例） |
| R2: ir-reference.md | Block 4 种 / Message / ModelResponse / StopReason / NormalizedUsage / StreamEvent 10 种 的构造签名 + to_dict() 输出 |
| R3: tool-api.md | 6 个工具的 JSON Schema 定义 + 参数表 + 返回值格式 + 错误类型 + 边界限制值 |
| R4: trajectory-events.md | 12 种 EventType 的触发条件 + payload 字段表 + JSONL 示例 + 常用 SQL 查询 |
| R5: cli-reference.md | 所有 flag / 子命令 / 环境变量 / 退出码的表 + 斜杠命令表 + 键位表 |

---

## Phase 5 — 补充（4 篇）

| 篇目 | 内容 |
|------|------|
| troubleshooting.md | 7 类问题：安装/API/工具/压缩/权限/内存/TUI/轨迹。每类：症状 → 诊断 → 解决方案 |
| faq.md | 10+ 个"为什么"：为什么自建 agent？为什么 4 层压缩？为什么不用 asyncio？怎么贡献？ |
| docs/adr/README.md | 15 篇 ADR 的索引表（编号 · 主题 · 状态 · 关联文档） |
| LICENSE | MIT license |

---

## 编写指导原则

1. **每篇开头：** 一句话说明谁需要读 + 前置阅读链接 + 读完能做什么
2. **每篇结尾：** 下一篇推荐链接 + 相关 ADR/文档链接
3. **风格：** Markdown。英文为主，关键术语首次出现时附中文注释
4. **代码块：** 标注语言（python、bash、json、sql、toml）
5. **图表：** 用 ASCII art 在行内绘制，不要外部图片引用
6. **文件引用：** 源文件行号标注（`loop.py:233`）
7. **交叉引用：** ADR = "为什么这样设计"；plans = "当初怎么实现"
8. **每篇独立：** 假设读者可能从任意一篇进入，在开头给出足够的上下文
9. **避免重复：** Phase 2 深潜中出现的概念在 Phase 0 已有精确定义，引用术语表即可
10. **渐进复杂度：** 00-02 不涉及代码细节；03 涉及代码结构但不过多；04-12 涉及具体实现

---

## 编写顺序建议（6 轮）

| 轮次 | 文档 | 预估量 |
|------|------|--------|
| 1 | Phase 0 + LICENSE + adr/README + README 增强 | ~1100 行 |
| 2 | 03 单轮详解 + 04 Runtime + 05 Tool | ~1650 行 |
| 3 | 06 Context + 07 Permission + 08 Memory | ~1350 行 |
| 4 | 09 Model + 10 Trajectory + 11 CLI/TUI + 12 Eval | ~1650 行 |
| 5 | 教程 T1+T2+T3+T4 | ~1550 行 |
| 6 | 参考 R1-R5 + troubleshooting + faq | ~1700 行 |

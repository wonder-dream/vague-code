# Architecture Overview

**谁需要读：** 想快速理解 vague-code 整体架构的读者
**前置阅读：** 01-terminology.md（术语）
**读完能做什么：** 知道所有子系统如何协作，能在哪个文件的哪个位置找到对应的代码

---

## 1. 一张图看懂——分层架构

vague-code 由 7 个子系统构成，组织为四层架构：

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CLI (Rich Renderer) + TUI (Textual) ──── thin shell                   │
│  cli/__init__.py     tui/app.py                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Agent Runtime (ReAct Loop + Retry + Checkpoint/Resume)                 │
│  loop.py:159-184 (Agent 类)    retry.py:52-72 (RetryPolicy)             │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────┐      │
│  │ Tool System │ │ Context  │ │Security  │ │ Memory System      │      │
│  │ 7 核心工具  │ │五层压缩  │ │4 种模式  │ │SQLite 统一记忆库   │      │
│  │ 并发调度    │ │KV Cache  │ │审计日志  │ │episodic 按需检索   │      │
│  │ 冲突可串行化│ │分层注入  │ │纯函数决策│ │增量蒸馏            │      │
│  │ tools.py    │ │context_  │ │permission│ │memory.py           │      │
│  │ concurrency │ │compress  │ │.py       │ │memory_tool.py      │      │
│  │ .py         │ │.py       │ │          │ │                    │      │
│  └─────────────┘ └──────────┘ └──────────┘ └────────────────────┘      │
│                          │                                              │
│  Model Abstraction ──────┴─── Codecs (DeepSeek / Anthropic) ────────→  │
│  ir.py (IR dataclass)     codecs/deepseek.py  codecs/anthropic.py      │
│  backend.py (ModelBackend Protocol)                                     │
├─────────────────────────────────────────────────────────────────────────┤
│  Trajectory (Event-Sourced JSONL → SQLite)                              │
│  trajectory.py:121-281  (Trajectory 类 + persist)                       │
├─────────────────────────────────────────────────────────────────────────┤
│  Repo Map (tree-sitter 符号索引) ── code_search 工具 + 地图注入        │
│  repomap.py                                                             │
├─────────────────────────────────────────────────────────────────────────┤
│  Eval Harness ── 30 tasks (SWE-bench Lite) ── Matrix ── Report         │
│  eval/cli.py  eval/harness.py  eval/matrix.py  eval/reporter.py        │
└─────────────────────────────────────────────────────────────────────────┘
```

各层的职责从上到下：

- **CLI/TUI（薄壳层）：** 提供命令行和终端界面。两者都是"薄 shell"——只做参数解析、API Key 管理、流式渲染，所有业务逻辑委托给 Agent Runtime。
- **Agent Runtime（核心层）：** 驱动 ReAct 循环，通过 4 个并行的子系统执行工具调用、管理上下文、检查权限、读写记忆。这是 vague-code 的大脑。
- **Model Abstraction（模型接入层）：** 统一 LLM 后端接入。自定义 IR（Internal Representation）屏蔽厂商差异，Codec 做翻译。
- **Trajectory（持久化层）：** 事件溯源存储。Agent 的每次运行自动保存为 JSONL 事件流，支持崩溃恢复和离线分析。
- **Eval Harness（评测层）：** 程序化驱动 Agent 完成评测任务，验证设计决策。独立于主代码库。

---

## 2. 跟踪一次请求——"fix the bug"的完整路径

当一个任务（比如"修一下 stats.py 的除零 bug"）从 CLI 传入，它在各子系统之间流转的完整时序：

```
CLI              Agent              Context            Backend          Codec
 │                 │                   │                  │               │
 ├─ task/workdir ─→│                   │                  │               │
 │                 ├─ start()          │                  │               │
│                 │  ├─ SystemPrompt  │                  │               │
│                 │  │  .build() ────→│                  │               │
│                 │  │               │← rules + ident  ─┤               │
│                 │  ├─ repo index  │                  │               │
│                 │  │  build + map │                  │               │
│                 ├─ _run_gen()      │                  │               │
 │                 │  ├─ compress_    │                  │               │
 │                 │  │  chain() ────→│ compress          │               │
 │                 │  │               │← (messages,     ─┤               │
 │                 │  │               │   reports)        │               │
 │                 │  ├─ backend.     │                  │               │
 │                 │  │  stream() ────│─────────────────→│── encode ──→│
 │                 │  │               │                  │               │
 │                 │  │               │                  │ SSE chunks ←─│
 │                 │  │               │                  │← decode ────│
 │                 │  │               │← StreamEvent    ─┤               │
 │                 │  │  yield ev ────│→ CLI 渲染        │               │
 │                 │  ├─ permission   │                  │               │
 │                 │  │  evaluate()   │                  │               │
 │                 │  ├─ execute_     │                  │               │
 │                 │  │  concurrent() │→ tool handler    │               │
 │                 │  ├─ checkpoint   │→ SQLite persist  │               │
 │                 │  └─ turn_box[0]  │                  │               │
 │                 │     +=1          │                  │               │
 │                 ├─ run_end        │                   │               │
 │←── Trajectory ──┤                  │                  │               │
```

每个阶段的关键函数：

1. **start()**（`loop.py:192`）：校验 registry key，初始化 Trajectory，构建 System Prompt，构建 repo index 并注入符号地图
2. **SystemPrompt.build()**（`context.py:9`）：三段式构造——角色层（静态）→ 知识层（规则文件）→ 动态层（当前任务上下文）
3. **compress_chain()**（`context_compress.py:30`）：五层压缩，从消息流中回收 token 空间
4. **backend.stream()**（`backend.py:85`）：调用 LLM 流式接口，Codec 负责 encode/decode
5. **permission evaluate()**（`permission.py:135`）：对每个 tool_use 做权限判定
6. **execute_concurrent()**（`concurrency.py:142`）：冲突可串行化调度并执行工具
7. **checkpoint persist()**（`loop.py:491`）：增量写入 SQLite
8. **turn loop**（`loop.py:241`）：turn_box[0] += 1，回到 while 继续下一轮

这个过程中有三个关键不变量贯穿始终（第 5 节详述）。

---

## 3. 数据住在哪里

不同类型的数据有各自的存储位置和生命周期：

| 数据 | 存储位置 | 格式 | 生命周期 | 创建点 | 读取点 |
|------|---------|------|---------|--------|--------|
| messages | 内存 `list[Message]` | IR Block 列表 | 单次 run | `loop.py:226-229` | `loop.py:268` |
| events | SQLite `runs` + `events` 表 | Event rows | 持久化 | `trajectory.py:187-196` `emit()` | `trajectory.py:130-185` `from_db()` |
| event JSONL | 文件系统 | JSONL | 按需导出 | `trajectory.py:252-255` `export_jsonl()` | 文本读取 |
| memories | `.agent/memory.md` | Markdown 分块 | 跨会话 | `memory_file.py` `append()` | `memory_file.py` `inject_text()`（注入） |
| rules | `.agent/rules.md` | Markdown | 项目级 | 用户手动创建 | `context_rules.py:19-40` `load_rules()` |
| config | AgentConfig → SQLite runs 表 | Python → JSON | 单次 run | `loop.py:210-216` | `trajectory.py:141-157` |
| permissions | `.agent/permission-rules.json` | JSON | 持久化 | `app.py:90-100` | `app.py:81-88` |
| trajectory events | SQLite `events` 表 | JSON payload | 持久化 | `loop.py:210-519` | `trajectory.py:171-181` |

关键观察：

- **messages 是瞬态的**——只在内存中，不做持久化。持久化的是 events（事件溯源），`to_messages()` 再从 events 重建 messages
- **memories 是跨会话的**——`.agent/memory.md` 文件式记忆，按项目物理隔离，system prompt 注入全文（限 200 行/25KB）
- **rules/permissions 是项目级的**——存储在项目 `.agent/` 目录下，跟随版本控制

---

## 4. 目录地图——vague_code/ 每个文件的职责

### agent/ 核心模块

| 文件 | 职责 | 核心类/函数 | 核心功能 |
|------|------|------------|---------|
| `agent/loop.py` | ReAct 主循环 | `Agent` / `_StreamAggregator` / `RunHandle` | Agent 生命周期、循环控制、检查点、恢复 |
| `agent/tools/` | 工具抽象层（ADR-0004） | `base.py: Tool` ABC / `fs.py` / `bash_tool.py` / `truncate.py` / `code_search.py` | class-based 工具：元数据声明（权限/并发）+ 模板方法 + 统一截断 + 结构化结果 |
| `agent/concurrency.py` | 冲突可串行化并发 | `schedule()` / `execute_concurrent()` / `ResourceScope` | scope 提取、冲突检测、分组调度、失败传播 |
| `agent/context.py` | 系统提示构建 + 压缩入口 | `SystemPrompt` / `compress_chain()` | 三段式 system prompt、公共压缩入口 |
| `agent/context_compress.py` | 五层压缩流水线 | `stale_snip()` / `microcompact()` / `structured_snip()` / `auto_compact()` / `truncate()` | 分层压缩纯函数 + 可观测报告 |
| `agent/context_tokens.py` | Token 计数 + 预算 | `count_tokens()` / `compute_budget()` / `per_message_tokens()` | tiktoken 精确 / 字符粗糙估算双路径 |
| `agent/context_rules.py` | 规则文件层级加载 | `load_rules()` | 向上遍历目录树收集 `.agent/rules.md` |
| `agent/permission.py` | 权限系统 | `evaluate()` / `Decision` / `PermissionMode` / `Operation` | 18 安全 + 24 危险命令分类、三层规则匹配 |
| `agent/memory_file.py` | 文件式记忆 | `MemoryFile` | `.agent/memory.md` 分块解析/追加（hash 去重）/移除/注入截尾 |
| `agent/repomap.py` | tree-sitter 符号索引 | `RepoIndex` / `Symbol` | 符号提取、引用计数热度、code_search、地图注入 |
| `agent/ir.py` | 自定义 IR dataclass | `Message` / `Block` 4 种 / `StopReason` / `StreamEvent` 10 种 / `ToolSpec` | 统一消息内部表示，序列化 |
| `agent/backend.py` | LLM 后端适配 | `ModelBackend` Protocol / `DeepSeekBackend` / `AnthropicBackend` | 统一 complete()/stream() 接口 |
| `agent/config.py` | 配置 dataclass | `AgentConfig` / `TransportConfig` / `CompressionConfig` / `MemoryConfig` / `RepoMapConfig` | 所有可配置字段集中管理 |
| `agent/retry.py` | 重试策略 + 错误分类 | `RetryPolicy` / `classify_llm_error()` / `response_signature()` | 指数退避、异常分类、token 估计 |
| `agent/trajectory.py` | 轨迹 + SQLite 存储 | `Trajectory` / `Event` / `EventType` | 事件溯源、增量 persist、JSONL 导出、恢复 |

### agent/codecs/ 编解码器

| 文件 | 职责 | 核心函数 | 核心功能 |
|------|------|---------|---------|
| `agent/codecs/deepseek.py` | DeepSeek codec | `encode_request()` / `decode_response()` / `DeepSeekStreamDecoder` | OpenAI 兼容编解码 + thinking 边界推断 |
| `agent/codecs/anthropic.py` | Anthropic codec | `encode_request()` / `decode_response()` / `AnthropicStreamDecoder` | 角色交替修正 + thinking 保留 + prompt caching |

### cli/ 命令行界面

| 文件 | 职责 | 核心类/函数 | 核心功能 |
|------|------|------------|---------|
| `cli/__init__.py` | CLI 入口 + TUI 子命令 | `main()` / `_tui_main()` | argparse 解析、API Key 管理、模式路由 |
| `cli/renderer.py` | Rich 流式渲染 | `RichStreamVisitor` | Console 流式打印、工具结果格式化 |

### tui/ 终端界面（v2 分层架构，详见 ADR-0019）

| 文件 | 职责 | 核心类/函数 | 核心功能 |
|------|------|------------|---------|
| `tui/app.py` | TUI 主应用（薄壳） | `VagueCodeApp` | compose/bindings、事件分发、回合管理、权限桥 |
| `tui/runner.py` | 同步 Agent ↔ 异步 UI 桥 | `VagueCodeAgentRunner` | 事件回调、取消、guidance、permission rules、resume |
| `tui/mixin.py` | 流式与活动动画 | `VagueCodeViewMixin` | Markdown 三层缓冲、thinking/streaming/running 动画、回合 metrics |
| `tui/state.py` | 展示态单一事实源 | `TuiTranscript` | entries + widget 引用、工具活动跟踪 |
| `tui/views/` | 纯函数渲染 | `topbar` / `activity` / `welcome` / `transcript` / `review` | 可独立单测的渲染层 |
| `tui/commands/` | 斜杠命令路由 | `CompositeCommandHandler` + 各 handler | `/resume /model /mode /permissions /save /new ...` |
| `tui/screens/permission.py` | 权限弹窗 | `PermissionDialog` | prewrite diff 预览 + 拒绝理由输入 |
| `tui/widgets/` | TUI 组件 | `ConversationView` / `ActivityLine` / `ComposerTextArea` / `VagueCodeMarkdown` | transcript 驱动渲染、活动行、多行输入、Markdown 选择门控 |

---

## 5. 关键不变量

整个系统有五个必须遵守的设计不变量。违反任何一个都会导致可预测的故障。

### 不变量 1：tool_use/tool_result 成对

LLM 发出的每个 tool_use 必须有对应的 tool_result，不能多不能少。

```
loop.py:409  messages.append(resp.message)     # assistant message：含 ToolUseBlock
loop.py:483  messages.append(tool_results)      # user message：含 ToolResultBlock
```

压缩层也必须保持配对（`context_compress.py:36-46` `_find_pairs()`）。违反的后果是 LLM API 报错——缺少 tool_result 的请求会被拒绝。

### 不变量 2：压缩纯函数

压缩的输入和输出都是 `messages`，不写数据库、不改 trajectory 事件流（ADR-0011 约束 1）。跨轮不持久化压缩结果——resume 时重建完整历史，下轮自然重新压缩。

违反后果：压缩泄漏到持久层 → resume 后消息混乱或重复。

### 不变量 3：权限纯函数

权限判定是纯函数：`evaluate(mode, operation, rules) → Decision`（`permission.py:135-160`）。不改全局状态、不写文件、不调外部服务。

违反后果：权限判定不可审计、不可复现，审计日志失去意义。

### 不变量 4：轨迹追加

`Trajectory.emit()` 只追加新事件（`trajectory.py:187-196`），不修改、不删除已有事件。`persist()` 增量写入 `_persisted_count` 之后的新事件（`trajectory.py:269-270`）。

违反后果：事件重复或丢失 → 轨迹不可逆，resume 行为不可预测。

### 不变量 5：Agent 零 asyncio

主循环全部同步：`while turn_box[0] < max_turns: ...`。并发走 `ThreadPoolExecutor`（`concurrency.py:171`），TUI 线程用 `@work(thread=True)` 隔离。

违反后果：同步/异步混合导致竞态条件，最难调试的那类 bug。

---

## 下一篇

→ **03-a-single-turn-explained.md**：把一次 Agent 循环展开到每个函数调用、每个数据变换。

**相关 ADR：** 0001（Agent 即库）、0002（自定义 IR/Codec）、0003（轨迹事件溯源）
**相关链接：** README.md、CONTEXT.md

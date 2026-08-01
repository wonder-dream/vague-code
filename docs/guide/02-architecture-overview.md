# 细纲：02-architecture-overview.md

**预估行数：** ~500 行（含 3 张 ASCII 图）
**定位：** 把 10 个子系统串起来，理解它们如何协作。

---

## 开头

- **谁需要读：** 想快速理解 XClaw 整体架构的读者
- **前置阅读：** 01-terminology.md（术语）
- **读完能做什么：** 知道所有子系统如何协作，能在哪个文件的哪个位置找到对应的代码

---

## 细纲

### 1. 一张图看懂——分层架构（~100 行，含 1 张主 ASCII 图）

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLI (Rich Renderer) + TUI (Textual) ──── thin shell              │
│  cli/__init__.py     tui/app.py                                    │
├─────────────────────────────────────────────────────────────────────┤
│  Agent Runtime (ReAct Loop + Retry + Checkpoint/Resume)             │
│  loop.py:159-184 (Agent 类)    retry.py:52-72 (RetryPolicy)         │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────┐  │
│  │ Tool System │ │ Context  │ │Security  │ │ Memory System      │  │
│  │ 7 核心工具  │ │五层压缩  │ │4 种模式  │ │SQLite 统一记忆库   │  │
│  │ 并发调度    │ │KV Cache  │ │审计日志  │ │episodic 检索    │  │
│  │ 冲突可串行化│ │分层注入  │ │纯函数决策│ │增量蒸馏            │  │
│  │ tools.py    │ │context_  │ │permission│ │memory.py           │  │
│  │ concurrency │ │compress  │ │.py       │ │memory_tool.py      │  │
│  │ .py         │ │.py       │ │          │ │                    │  │
│  └─────────────┘ └──────────┘ └──────────┘ └────────────────────┘  │
│                          │                                          │
│  Model Abstraction ──────┴─── Codecs (DeepSeek / Anthropic) ────→  │
│  ir.py (IR dataclass)     codecs/deepseek.py  codecs/anthropic.py  │
│  backend.py (ModelBackend Protocol)                                 │
├─────────────────────────────────────────────────────────────────────┤
│  Trajectory (Event-Sourced JSONL → SQLite)                          │
│  trajectory.py:121-281  (Trajectory 类 + persist)                   │
├─────────────────────────────────────────────────────────────────────┤
│  Eval Harness ── 30 tasks (SWE-bench Lite) ── Matrix ── Report     │
│  eval/cli.py  eval/harness.py  eval/matrix.py  eval/reporter.py    │
└─────────────────────────────────────────────────────────────────────┘
```

- **核心素材：** `README.md` 第 11-29 行已有架构图，此图为其细化版

### 2. 跟踪一次请求——"fix the bug"的完整路径（~120 行，含 ASCII 时序图）

```
CLI              Agent              Context            Backend          Codec
 │                 │                   │                  │               │
 ├─ task/workdir ─→│                   │                  │               │
 │                 ├─ start()          │                  │               │
 │                 │  ├─ SystemPrompt  │                  │               │
 │                 │  │  .build() ────→│                  │               │
 │                 │  │               │← rules + ident  ─┤               │
  │                 │  ├─ repo index  │                  │               │
 │                 │  │  memory inject│                  │               │
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

- 每个阶段标注文件名和关键函数名（如 `loop.py:192` `start()`、`context.py:9` `SystemPrompt.build()`）
- 强调三个关键不变量（第 5 节详述）

### 3. 数据住在哪里（~80 行）

一张存储位置与生命周期表：

| 数据 | 存储位置 | 格式 | 生命周期 | 创建点 | 读取点 |
|------|---------|------|---------|--------|--------|
| messages | 内存 `list[Message]` | IR Block 列表 | 单次 run | `loop.py:226-229` | `loop.py:268` |
| events | SQLite `runs` + `events` 表 | Event rows | 持久化 | `trajectory.py:187-196` `emit()` | `trajectory.py:130-185` `from_db()` |
| event JSONL | 文件系统 | JSONL | 按需导出 | `trajectory.py:252-255` `export_jsonl()` | 文本读取 |
| memories | SQLite `memory.db` | FTS5 索引 | 跨会话 | `memory.py:34-64` `ingest()` | `memory.py:66-86` `search()` |
| rules | `.agent/rules.md` | Markdown 文本 | 项目级 | 用户手动创建 | `context_rules.py:19-40` `load_rules()` |
| config | `AgentConfig` dataclass → SQLite runs 表 | Python 对象 → JSON | 单次 run | `loop.py:210-216` | `trajectory.py:141-157` |
| permissions | `.agent/permission-rules.json` | JSON | 持久化 | `app.py:90-100` | `app.py:81-88` |
| trajectory events | SQLite `events` 表 | JSON payload | 持久化 | `loop.py:210-519` | `trajectory.py:171-181` |

### 4. 目录地图（~100 行）

`src/` 每个文件的职责表（文件名 · 职责 · 核心类/函数 · 核心功能描述）：

| 文件 | 职责 | 核心类/函数 | 核心功能 |
|------|------|------------|---------|
| `agent/loop.py` | ReAct 主循环 | `Agent` / `_StreamAggregator` / `RunHandle` | Agent 生命周期、循环控制、检查点、恢复 |
| `agent/tools.py` | 8 个工具（6 基础 + 2 动态） | `Tool` / `DEFAULT_TOOLS` / 6 个 factory 函数 | 工具定义 + 工厂模式 + JSON Schema |
| `agent/concurrency.py` | 冲突可串行化并发 | `schedule()` / `execute_concurrent()` / `ResourceScope` | scope 提取、冲突检测、分组调度、失败传播 |
| `agent/context.py` | 系统提示构建 + 压缩入口 | `SystemPrompt` / `compress_chain()` | 三段式 system prompt、公共压缩入口 |
| `agent/context_compress.py` | 五层压缩流水线 | `stale_snip()` / `microcompact()` / `structured_snip()` / `auto_compact()` / `truncate()` | 分层压缩纯函数 + 可观测报告 |
| `agent/context_tokens.py` | Token 计数 + 预算 | `count_tokens()` / `compute_budget()` / `per_message_tokens()` | tiktoken 精确计数 / 字符粗糙估算双路径 |
| `agent/context_rules.py` | 规则文件层级加载 | `load_rules()` | 向上遍历目录树收集 `.agent/rules.md` |
| `agent/permission.py` | 权限系统 | `evaluate()` / `Decision` / `PermissionMode` / `Operation` | 18 安全 + 24 危险命令分类、三层规则匹配 |
| `agent/memory.py` | 记忆存储 | `MemoryStore` | SQLite 统一记忆库、SHA-256 去重、LIKE 检索 + 热度排序 |
| `agent/memory_tool.py` | memory_search 工具 | `MEMORY_SEARCH_SPEC` / `make_memory_search_handler()` | 动态注入工具，memory 开启时注册 |
| `agent/repomap.py` | tree-sitter 符号索引 | `RepoIndex` / `Symbol` | 符号提取、引用计数热度、code_search、地图注入 |
| `agent/ir.py` | 自定义 IR dataclass | `Message` / `Block` 4 种 / `StopReason` / `StreamEvent` 10 种 / `ToolSpec` | 统一消息内部表示，序列化 |
| `agent/backend.py` | LLM 后端适配层 | `ModelBackend` Protocol / `DeepSeekBackend` / `AnthropicBackend` | 统一 complete()/stream() 接口 |
| `agent/config.py` | 配置 dataclass | `AgentConfig` / `TransportConfig` / `CompressionConfig` / `MemoryConfig` / `RepoMapConfig` | 所有可配置字段集中管理 |
| `agent/retry.py` | 重试策略 + 错误分类 | `RetryPolicy` / `classify_llm_error()` / `response_signature()` | 指数退避、异常分类、token 估计 |
| `agent/trajectory.py` | 轨迹事件流 + SQLite 存储 | `Trajectory` / `Event` / `EventType` | 事件溯源、增量 persist、JSONL 导出、崩溃恢复 |
| `agent/codecs/deepseek.py` | DeepSeek codec | `encode_request()` / `decode_response()` / `DeepSeekStreamDecoder` | OpenAI 兼容格式编解码 + thinking 边界推断 |
| `agent/codecs/anthropic.py` | Anthropic codec | `encode_request()` / `decode_response()` / `AnthropicStreamDecoder` | 角色交替修正 + thinking 块保留 + prompt caching |
| `cli/__init__.py` | CLI 入口 + TUI 子命令 | `main()` / `_tui_main()` | argparse 参数解析、API Key 解析、模式路由 |
| `cli/renderer.py` | Rich 流式渲染 | `RichStreamVisitor` | Console 流式打印、工具结果格式化 |
| `tui/app.py` | TUI 主应用 | `XClawApp` | Textual App、线程桥接、权限弹窗、侧边栏 |
| `tui/visitor.py` | Textual 流式渲染 | `TextualStreamVisitor` | 可折叠 thinking/tool result 渲染 |
| `tui/screens/` | TUI 弹窗屏幕 | `PermissionDialog` / `SessionDetail` / `HelpScreen` | 权限确认、会话详情、帮助 |
| `tui/widgets/` | TUI 组件 | `ConversationView` / `Sidebar` / `StatusBar` / `CommandInput` | 四个布局区域的 widget 实现 |

### 5. 关键不变量（~80 行）

**不变量 1：tool_use/tool_result 成对**
- `loop.py:409` `messages.append(resp.message)` 中 assistant message 包含 ToolUseBlock
- `loop.py:483` `messages.append(Message(role="user", content=tool_results))` 追加 ToolResultBlock
- 压缩层也不破坏配对（`context_compress.py:36-46` `_find_pairs()`）
- 违反后果：LLM API 报错（缺少 tool_result）

**不变量 2：压缩纯函数**
- `messages → messages`，不写数据库、不改 trajectory 事件流（ADR-0011 约束 1）
- 跨轮不持久化，resume 重建完整历史后下轮自然重新压缩
- 违反后果：压缩泄漏到持久层 → resume 后消息混乱

**不变量 3：权限纯函数**
- `evaluate(mode, operation, rules) → Decision`（`permission.py:135-160`）
- 不改全局状态、不写文件、不调外部服务
- 违反后果：权限判定不可审计、不可复现

**不变量 4：轨迹追加**
- `Trajectory.emit()` 只追加新事件（`trajectory.py:187-196`）
- `persist()` 增量写入 `_persisted_count` 之后的新事件（`trajectory.py:269-270`）
- 违反后果：事件重复或丢失 → 轨迹不可逆

**不变量 5：Agent 零 asyncio**
- 主循环全部同步（`while turn_box[0] < max_turns: ...`）
- 并发走 `ThreadPoolExecutor`（`concurrency.py:171`），TUI 线程用 `@work(thread=True)` 隔离
- 违反后果：同步/异步混合导致竞态条件

---

## 结尾

**下一篇推荐：** → 03-a-single-turn-explained.md（把一次 Agent 循环展开到每个函数调用）
**相关 ADR：** 0001（Agent 即库）、0002（自定义 IR/Codec）、0003（轨迹事件溯源）
**相关链接：** README.md、CONTEXT.md

---

## 本文件说明

这是文档 `02-architecture-overview.md` 的细纲（大纲）。含 1 张主架构 ASCII 图 + 1 张时序 ASCII 图，实际写作时需确保图与代码行号一一对应。子系统和文件目录部分在写作时逐项核对实际代码。

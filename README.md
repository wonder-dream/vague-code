# XClaw

面向真实编码场景的轻量级本地 Coding Agent CLI。自研 Agent Runtime、可控工具系统、四层上下文压缩、冲突可串行化并发调度、权限安全体系与跨会话记忆，以及配套的自动化评测工具链。

Powered by **DeepSeek V4 Flash** (or any OpenAI/Anthropic compatible backend).

---

## 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLI (Rich Renderer) + TUI (Textual) ──── thin shell              │
├─────────────────────────────────────────────────────────────────────┤
│  Agent Runtime (ReAct Loop + Retry + Checkpoint/Resume)             │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────┐  │
│  │ Tool System │ │ Context  │ │Security  │ │ Memory System      │  │
│  │ 7 核心工具  │ │五层压缩  │ │4 种模式  │ │SQLite 统一记忆库   │  │
│  │ 并发调度    │ │KV Cache  │ │审计日志  │ │episodic 按需检索   │  │
│  │ 冲突可串行化│ │分层注入  │ │纯函数决策│ │增量蒸馏            │  │
│  └─────────────┘ └──────────┘ └──────────┘ └────────────────────┘  │
│                          │                                          │
│  Model Abstraction ──────┴─── Codecs (DeepSeek / Anthropic) ────→  │
│  Custom IR (text/thinking/tool_use/tool_result) + Unified Stream    │
├─────────────────────────────────────────────────────────────────────┤
│  Trajectory (Event-Sourced JSONL → SQLite)                          │
├─────────────────────────────────────────────────────────────────────┤
│  Repo Map (tree-sitter 符号索引) ── code_search 工具 + 地图注入     │
├─────────────────────────────────────────────────────────────────────┤
│  Eval Harness ── 31 tasks (官方保留) ── 真验收/pass^k ── Report     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 快速开始

```bash
# 环境
uv sync                           # 安装依赖
export DEEPSEEK_API_KEY=sk-xxx    # 设置 API Key

# 运行 Agent（CLI 模式）
python -m src.cli "Fix the bug in stats.py"
python -m src.cli --provider anthropic "Refactor auth module"

# 运行 Agent（TUI 模式 — 全屏交互界面）
xcode tui "Fix the bug in stats.py"
xcode tui --model deepseek-v4-pro "Analyze the project structure"

# 运行评测（现行体系：真验收 + sanity gate + pass^k，详见 eval/README.md）
python -m eval.cli --tasks eval/tasks.json --fake          # 验证框架
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --max-turns 25  # 真实 API（20 题本机可跑）
python -m eval.cli --tasks eval/tasks.json --repeat 3 --out report.md  # 消融实验
```

---

## TUI 模式

`xcode tui [task]` 启动全屏交互式终端界面（基于 Textual 框架，v2 分层架构见 `docs/adr/0019-tui-v2-architecture.md`）。

### 布局
- **Topbar** — brand · 运行状态 · provider/model · 权限模式 · 工作目录（窄屏自动截断）
- **Conversation View** — 流式 Markdown 渲染；消息类型左边线分色（用户=青 / 助手=绿 / 工具=灰 / 权限=琥珀 / 错误=红）；thinking 超长自动折叠（`T` 展开）
- **Activity Line** — 状态动画（`thinking [..] / running [=  ] / streaming [>  ]`）+ 回合指标（`12.3s · 2 tools`）
- **Composer** — 多行输入（`Shift+Enter` 换行），运行中发送进入 guidance 队列，下一轮生效

### 键绑定

| 键 | 操作 |
|-------|--------|
| `Enter` | 发送 |
| `Shift+Enter` | 换行 |
| `↑` / `↓` | 输入历史（焦点在输入框时） |
| `Esc` | 聚焦输入框；**运行中按两次**（1 秒内）中断当前回合 |
| `Ctrl+C` | 有选中先复制 → 运行中中断 → 否则退出 |
| `T` | 折叠/展开 thinking |
| `F1` | 帮助 |

### 斜杠命令

| 命令 | 操作 |
|-------|--------|
| `/help` | 帮助（同 `F1`） |
| `/resume` | picker 选择历史会话继续（重放历史后恢复运行） |
| `/new` | 清空并开始新会话 |
| `/clear` | 清空对话视图 |
| `/save [path]` | 导出 trajectory 为 JSONL |
| `/model` / `/model <name>` | picker 或直接切换模型 |
| `/mode <m>` | 设置权限模式 `safe\|normal\|autoedit\|auto` |
| `/permissions` | 列出持久化权限规则 |
| `/quit` | 退出 |

### 权限
交互式确认对话框：`Y` 允许一次，`Ctrl+Y` 始终允许并持久化规则至 `.agent/permission-rules.json`，`N` 拒绝。
对 `write_file`/`patch` 会先展示**写入前 diff 预览**（红删绿增）；拒绝时可填理由，理由会回传给模型（反馈闭环）。

### 选项
```
xcode tui [task] [--model] [--max-turns] [--db-path] [--provider] [--timeout-s]
```

> 提示：`xcode` 命令需要激活虚拟环境（`uv run xcode tui ...` 或先 `.venv\Scripts\Activate.ps1`）。

---

## 核心模块

| 模块 | 文件 | 描述 |
|------|------|------|
| **Agent Loop** | `src/agent/loop.py` | ReAct 循环：LLM → tool_use → tool_result → LLM，含 retry/checkpoint/resume |
| **Tool System** | `src/agent/tools.py` | 7 个工具（read/write/patch/glob/grep/bash/code_search），JSON Schema 校验，50K 截断 |
| **Concurrency** | `src/agent/concurrency.py` | 冲突可串行化 ThreadPool 调度，资源 scope 提取 + 冲突检测 |
| **Context Engineering** | `src/agent/context_compress.py` | 五层压缩：stale_snip → microcompact → structured_snip → auto_compact → truncation |
| **Permission System** | `src/agent/permission.py` | 4 种模式（safe/normal/autoedit/auto）+ 24 类危险命令正则 |
| **Memory System** | `src/agent/memory.py` | SQLite 统一记忆库 + episodic 检索注入 + auto-compact 蒸馏 |
| **Repo Map** | `src/agent/repomap.py` | tree-sitter 符号索引，code_search 工具 + 符号地图注入 |
| **Model Abstraction** | `src/agent/codecs/` | 自定义 IR → DeepSeek/Anthropic codec，统一流式事件 |
| **Trajectory** | `src/agent/trajectory.py` | Event-sourced JSONL → SQLite 事件流，to_messages() 导出 |
| **CLI** | `src/cli/` | argparse + Rich 渲染，--stream/--no-stream，--provider |
| **Eval Harness** | `eval/` | 官方保留 31 题 + 真验收/sanity gate + pass^k + Markdown 报告 |

---

## 评测结果

> ⚠️ 早期 v0.1 的 83%/93% 等数字基于**假 pass/fail**（验收测试未实跑），已废弃且不得引用。
> 2026-08 起评测体系按 `docs/plans/0016-eval-methods.md` 全面升级：真验收（sanity gate 双检 +
> F2P/P2P 实跑）、pass^k 可靠性、任务集按 OpenAI SWE-bench Verified 官方标注重建。

**现状（详见 `docs/handoff/2026-08-03-xclaw-eval-system.md`）：**
- 任务集 **31 题**（全部官方保留，17 道脏题已剔除），本机可跑 **20 题**（sympy 17 + sphinx 2 + pytest 1）
- 环境策展 5 仓实证验证（sanity gate 全过）；sklearn/astropy 10 题需 MSVC/Linux CI
- **真数字待 20 题基线消融产出**：`python -m eval.cli --tasks eval/tasks.json --max-turns 25 --repeat 3`
- 单一实证：合成任务端到端 `verified=True`（Agent 真修好 bug，judge 5/5）

---

## 项目结构

```
xcode/
├── src/agent/                 # Agent 核心包
│   ├── loop.py                # ReAct 主循环
│   ├── tools.py               # 6 个基础工具 + code_search spec
│   ├── concurrency.py         # 冲突可串行化并发
│   ├── context_compress.py    # 五层压缩流水线
│   ├── context.py             # 系统提示构建
│   ├── context_tokens.py      # Token 计数 + 预算
│   ├── context_rules.py       # 规则文件层级加载
│   ├── permission.py          # 权限系统
│   ├── memory.py              # 记忆系统
│   ├── memory_tool.py         # memory_search 工具
│   ├── repomap.py             # tree-sitter 符号索引（code_search + 地图注入）
│   ├── config.py              # AgentConfig 配置
│   ├── ir.py                  # 自定义 IR dataclass
│   ├── backend.py             # LLM 后端适配层
│   └── codecs/                # 厂商 codec
│       ├── deepseek.py        # DeepSeek codec
│       └── anthropic.py       # Anthropic codec
├── eval/                      # 评测框架
│   ├── cli.py                 # 评测 CLI 入口
│   ├── harness.py             # Agent 驱动 + 真验收（verify/metrics 接入）
│   ├── env.py                 # 每 repo venv 策展（REPO_SETUP）
│   ├── verify.py              # 验收执行器（sanity gate 双检 + F2P/P2P）
│   ├── matrix.py              # 实验矩阵展开
│   ├── reporter.py            # Markdown 报告（pass^k + 失败分布）
│   ├── metrics.py             # 确定性轨迹指标
│   ├── judge.py / rubric.py   # LLM-as-Judge（离线）
│   ├── classify.py            # 八类失败分类
│   ├── audit_tasks.py / audit_ui.py  # 任务质量筛查（HTML 打分页）
│   ├── select_verified_tasks.py      # 官方保留题选择器
│   └── tasks.json             # 31 题任务集（20 题本机可跑）
├── cli/                       # CLI 薄壳
│   ├── __init__.py            # Args + backend 创建
│   └── renderer.py            # Rich 流式渲染
├── docs/                      # 文档
│   ├── adr/                   # 18 份架构决策记录
│   ├── plans/                 # 16 份实现计划
│   ├── articles/              # 24 篇成品文章（含 README 索引）
│   ├── handoff/               # 会话交接记录
│   └── known-issues.md        # 已知问题跟踪
└── tests/                     # 586 条自动化测试
```

---

## 技术栈

| 层 | 选型 |
|------|---------|
| 语言 | Python 3.12 |
| LLM API | DeepSeek / Anthropic / OpenAI 兼容 |
| 记忆存储 | SQLite + FTS5 (BM25) |
| 代码索引 | tree-sitter 0.26 + tree-sitter-python |
| 代码质量 | ruff + mypy + pytest (516 tests) |
| CLI | argparse + Rich |
| 依赖管理 | uv |

---

## 相关知识

- [Agent 架构决策目录](docs/adr/) — 14 份 ADR，覆盖所有模块设计决策
- [完整实现计划](docs/plans/) — 12 份，逐步骤实现
- [已知问题](docs/known-issues.md) — 未修复的低优问题跟踪

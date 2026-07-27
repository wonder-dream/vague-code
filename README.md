# XClaw

面向真实编码场景的轻量级本地 Coding Agent CLI。自研 Agent Runtime、可控工具系统、四层上下文压缩、冲突可串行化并发调度、权限安全体系与跨会话记忆，以及配套的自动化评测工具链。

Powered by **DeepSeek V4 Flash** (or any OpenAI/Anthropic compatible backend).

---

## 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLI (Rich Renderer) ──── thin shell                               │
├─────────────────────────────────────────────────────────────────────┤
│  Agent Runtime (ReAct Loop + Retry + Checkpoint/Resume)             │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────┐  │
│  │ Tool System │ │ Context  │ │Security  │ │ Memory System      │  │
│  │ 6 核心工具  │ │四层压缩  │ │4 种模式  │ │SQLite 统一记忆库   │  │
│  │ 并发调度    │ │KV Cache  │ │审计日志  │ │pinned + episodic   │  │
│  │ 冲突可串行化│ │分层注入  │ │纯函数决策│ │增量蒸馏            │  │
│  └─────────────┘ └──────────┘ └──────────┘ └────────────────────┘  │
│                          │                                          │
│  Model Abstraction ──────┴─── Codecs (DeepSeek / Anthropic) ────→  │
│  Custom IR (text/thinking/tool_use/tool_result) + Unified Stream    │
├─────────────────────────────────────────────────────────────────────┤
│  Trajectory (Event-Sourced JSONL → SQLite)                          │
├─────────────────────────────────────────────────────────────────────┤
│  Eval Harness ── 30 tasks (SWE-bench Lite) ── Matrix ── Report     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 快速开始

```bash
# 环境
uv sync                           # 安装依赖
export DEEPSEEK_API_KEY=sk-xxx    # 设置 API Key

# 运行 Agent
python -m src.cli "Fix the bug in stats.py"
python -m src.cli --provider anthropic "Refactor auth module"

# 运行评测
python -m eval.cli --tasks eval/tasks.json --fake          # 验证框架
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash  # 真实 API
python -m eval.cli --tasks eval/tasks.json --repeat 3 --out report.md  # 消融实验
```

---

## 核心模块

| 模块 | 文件 | 描述 |
|------|------|------|
| **Agent Loop** | `src/agent/loop.py` | ReAct 循环：LLM → tool_use → tool_result → LLM，含 retry/checkpoint/resume |
| **Tool System** | `src/agent/tools.py` | 6 个工具（read/write/patch/glob/grep/bash），JSON Schema 校验，50K 截断 |
| **Concurrency** | `src/agent/concurrency.py` | 冲突可串行化 ThreadPool 调度，资源 scope 提取 + 冲突检测 |
| **Context Engineering** | `src/agent/context_compress.py` | 四层压缩：stale_snip → microcompact → auto_compact → truncation |
| **Permission System** | `src/agent/permission.py` | 4 种模式（safe/normal/autoedit/auto）+ 24 类危险命令正则 |
| **Memory System** | `src/agent/memory.py` | SQLite FTS5 统一记忆库 + pinned/episodic 注入 + auto-compact 蒸馏 |
| **Model Abstraction** | `src/agent/codecs/` | 自定义 IR → DeepSeek/Anthropic codec，统一流式事件 |
| **Trajectory** | `src/agent/trajectory.py` | Event-sourced JSONL → SQLite 事件流，to_messages() 导出 |
| **CLI** | `src/cli/` | argparse + Rich 渲染，--stream/--no-stream，--provider |
| **Eval Harness** | `eval/` | SWE-bench 格式 30 题 + 实验矩阵 + Markdown 报告 |

---

## 评测结果

基于 **SWE-bench Lite 抽取的 30 题**，**DeepSeek V4 Flash**，max_turns=30。

### 基线（无压缩、无并发，30 题）
- Pass rate: **60%**（18/30 end_turn）
- Avg tokens: **931K** / task

### 消融实验（10 题 × 4 配置 × 3 重复 = 120 run）

| Compression | Concurrency | Pass Rate | Avg Tokens | 对比基线 |
|------------|-------------|-----------|------------|----------|
| ✗ | ✗ | 83% | 635K | +23pp baseline |
| ✗ | ✓ | **93%** | **614K** | **+33pp, -34% tokens** |
| ✓ | ✗ | 76% | 735K | +16pp |
| ✓ | ✓ | 73% | 759K | +13pp |

> 并发提升最大（93% pass rate），同时 token 消耗最低；压缩在短会话（<30 turns）中效果有限，auto_compact 的 LLM 调用成本高于回收收益。压缩设计目标为 30+ 轮长会话。

---

## 项目结构

```
xcode/
├── src/agent/                 # Agent 核心包
│   ├── loop.py                # ReAct 主循环
│   ├── tools.py               # 6 个核心工具
│   ├── concurrency.py         # 冲突可串行化并发
│   ├── context_compress.py    # 四层压缩流水线
│   ├── context.py             # 系统提示构建
│   ├── context_tokens.py      # Token 计数 + 预算
│   ├── context_rules.py       # 规则文件层级加载
│   ├── permission.py          # 权限系统
│   ├── memory.py              # 记忆系统
│   ├── memory_tool.py         # memory_search 工具
│   ├── config.py              # AgentConfig 配置
│   ├── ir.py                  # 自定义 IR dataclass
│   ├── backend.py             # LLM 后端适配层
│   └── codecs/                # 厂商 codec
│       ├── deepseek.py        # DeepSeek codec
│       └── anthropic.py       # Anthropic codec
├── eval/                      # 评测框架
│   ├── cli.py                 # 评测 CLI 入口
│   ├── harness.py             # Agent + 验收
│   ├── matrix.py              # 实验矩阵展开
│   ├── reporter.py            # Markdown 报告生成
│   ├── select_tasks.py        # SWE-bench 任务筛选
│   └── tasks.json             # 30 题任务集
├── cli/                       # CLI 薄壳
│   ├── __init__.py            # Args + backend 创建
│   └── renderer.py            # Rich 流式渲染
├── docs/                      # 文档
│   ├── adr/                   # 14 份架构决策记录
│   ├── plans/                 # 12 份实现计划
│   └── known-issues.md        # 已知问题跟踪
└── tests/                     # 448 条自动化测试
```

---

## 技术栈

| 层 | 选型 |
|------|---------|
| 语言 | Python 3.12 |
| LLM API | DeepSeek / Anthropic / OpenAI 兼容 |
| 记忆存储 | SQLite + FTS5 (BM25) |
| 代码质量 | ruff + mypy + pytest (448 tests) |
| CLI | argparse + Rich |
| 依赖管理 | uv |

---

## 相关知识

- [Agent 架构决策目录](docs/adr/) — 14 份 ADR，覆盖所有模块设计决策
- [完整实现计划](docs/plans/) — 12 份，逐步骤实现
- [已知问题](docs/known-issues.md) — 未修复的低优问题跟踪

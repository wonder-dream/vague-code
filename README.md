# vague-code

面向真实编码场景的轻量级本地 Coding Agent CLI。自研 Agent Runtime、可控工具系统、五层上下文压缩、冲突可串行化并发调度、权限安全体系与跨会话记忆，以及配套的自动化评测工具链。

支持 **DeepSeek / OpenAI（GPT 系列）/ Anthropic / 任意 OpenAI 兼容端点**（中转站、OpenRouter 等），CLI 与 TUI 双前端。

---

## 30 秒上手（DeepSeek 默认）

```bash
# 1. 安装（Python ≥ 3.12，支持 Windows / Linux）
pip install vague-code

# 2. 创建 API Key 配置文件（在当前工作目录）
#    在 DeepSeek 开放平台 https://platform.deepseek.com 创建 Key
echo "DEEPSEEK_API_KEY=sk-你的key" > .env

# 3. 运行（CLI 模式）
vague-code "Fix the bug in stats.py"

# 4. 或全屏交互界面（TUI 模式）
vague-code tui "Fix the bug in stats.py"
```

---

## 支持的 API 一览

| 方案 | Key 环境变量 | 默认端点 | 启动参数 | 获取 Key |
|---|---|---|---|---|
| **① DeepSeek 官方**（默认） | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` | `--provider deepseek`（可省略） | platform.deepseek.com |
| **② OpenAI 官方（GPT 系列）** | `OPENAI_API_KEY` | `https://api.openai.com/v1` | `--provider openai` | platform.openai.com |
| **③ Anthropic** | `ANTHROPIC_API_KEY` | DeepSeek 的 Anthropic 兼容端点 | `--provider anthropic` | DeepSeek 平台（兼容端点） |
| **④ 任意 OpenAI 兼容端点**（中转站 / OpenRouter / Moonshot / Codex 中转） | 自定义（如 `RELAY_KEY`） | 自定义 | `--base-url <URL> --api-key-env <变量名>` | 各服务商后台 |

> 三条命令行界面（CLI 单次 / TUI 全屏 / chat 对话）的参数完全一致，下文命令中的 `vague-code` 均可替换为 `vague-code tui` 或 `vague-code chat`。

---

## 通用配置方法（任选其一）

vague-code 按顺序读取：**当前工作目录的 `.env` 文件** → **系统环境变量**。两种方式二选一即可。

**方式 A：`.env` 文件（推荐，跨平台、不污染系统）**

在你要运行 Agent 的目录创建 `.env`：

```ini
# 想用哪个方案就填哪个 key
DEEPSEEK_API_KEY=sk-xxxxxxxx
# OPENAI_API_KEY=sk-proj-xxxxxxxx
# ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
# RELAY_KEY=sk-xxxxxxxx            # 中转站自定义变量名
```

**方式 B：系统环境变量**

```bash
# Linux / macOS
export DEEPSEEK_API_KEY=sk-xxxxxxxx
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "sk-xxxxxxxx"
# Windows cmd
set DEEPSEEK_API_KEY=sk-xxxxxxxx
```

---

## 方案①：DeepSeek 官方（默认）

**第 1 步**：在 https://platform.deepseek.com 注册并创建 API Key。

**第 2 步**：配置 Key（`.env` 或环境变量，见上）。

**第 3 步**：运行。

```bash
# 默认模型 deepseek-v4-flash
vague-code "修复 stats.py 的 bug"
vague-code tui "分析项目结构"
vague-code chat                      # 交互式连续对话

# 指定模型（deepseek-v4-pro / deepseek-chat / deepseek-reasoner）
vague-code --model deepseek-v4-pro "重构 auth 模块"
```

**验证**：看到模型回复即成功。常见错误 `DEEPSEEK_API_KEY not found` → 检查 `.env` 文件是否在当前目录、变量名是否正确。

---

## 方案②：OpenAI 官方（GPT 系列）

**第 1 步**：在 https://platform.openai.com 创建 API Key（形如 `sk-proj-...`）。

**第 2 步**：配置 Key。

```bash
echo "OPENAI_API_KEY=sk-proj-xxxxxxxx" > .env
```

**第 3 步**：运行。

```bash
vague-code --provider openai --model gpt-5.6-sol "修复这个 bug"
vague-code tui --provider openai --model gpt-5.6-terra "重构项目结构"
```

**支持的模型（OpenAI 现行文本模型，2026-08）**：

| 模型 | 说明 | 上下文窗口 |
|---|---|---|
| `gpt-5.6-sol`（别名 `gpt-5.6`） | 旗舰，复杂推理与编码 | 1.05M |
| `gpt-5.6-terra` | 智能与成本均衡 | 1.05M |
| `gpt-5.6-luna` | 成本敏感、高吞吐 | 1.05M |

> GPT-5.6 系列自动匹配 o200k 词表与 1.05M 窗口预算；历史上 gpt-4o/gpt-4.1/gpt-5 等已退场的模型名不再推荐（老 gpt-4/3.5 仍按 cl100k 词表兼容计数）。任何其他 GPT 模型名都能直接传，未知命名自动套用 o200k 词表与 128K 兜底窗口。

---

## 方案③：Anthropic

**第 1 步**：创建 Key。

- 默认指向 **DeepSeek 的 Anthropic 兼容端点**（`https://api.deepseek.com/anthropic`），Key 同样在 DeepSeek 平台获取，填到 `ANTHROPIC_API_KEY`
- 想连 Anthropic 官方或第三方 Anthropic 端点：用 `--base-url` 覆盖端点，Key 用对应平台的

**第 2 步**：配置 Key（`.env` 或环境变量）。

**第 3 步**：运行。

```bash
vague-code --provider anthropic "重构 auth 模块"                    # DeepSeek 兼容端点
vague-code --provider anthropic --base-url https://api.anthropic.com "..."  # 官方端点
```

---

## 方案④：任意 OpenAI 兼容端点（中转站 / OpenRouter / Codex 中转）

适合：中转站 Key、OpenRouter、Moonshot、任何提供 OpenAI 兼容接口的服务。

### 推荐做法：配置文件声明一次，日常零参数

**第 1 步**：生成配置模板。

```bash
vague-code init
```

**第 2 步**：编辑生成的 `vague-code.json`，把中转站信息填进 `providers`（名字随意，这里叫 `my-relay`），并设为默认：

```json
{
  "defaultProvider": "my-relay",
  "defaultModel": "gpt-5.6-sol",
  "providers": {
    "deepseek": { "baseUrl": "https://api.deepseek.com", "apiKeyEnv": "DEEPSEEK_API_KEY" },
    "openai":   { "baseUrl": "https://api.openai.com/v1", "apiKeyEnv": "OPENAI_API_KEY" },
    "my-relay": {
      "baseUrl": "https://code.newcli.com/codex/v1",
      "apiKeyEnv": "RELAY_KEY",
      "models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
    }
  }
}
```

> **协议选择**：默认 `openai`（Chat Completions）。若中转站只支持 Responses API
> （如部分 Codex 中转，Codex CLI 配置里 `wire_api = "responses"`），加一行
> `"protocol": "responses"` 即可，其余配置不变：
>
> ```json
> "my-relay": {
>   "baseUrl": "https://你的codex中转/v1",
>   "apiKeyEnv": "RELAY_KEY",
>   "protocol": "responses",
>   "models": ["gpt-5.6-sol"]
> }
> ```

**第 3 步**：`.env` 里存 key（变量名与 `apiKeyEnv` 一致）。

```ini
RELAY_KEY=sk-xxxxxxxx
```

**第 4 步**：日常使用——零参数直接跑，`--provider`/`--model` 按需覆盖。

```bash
vague-code tui                     # 自动用 my-relay + gpt-5.6-sol
vague-code "修复这个bug"           # CLI 单次
vague-code tui --model gpt-5.6-terra   # 只换模型
vague-code tui --provider openai       # 切到内置 OpenAI
```

> 配置也可放全局 `~/.config/vague-code/config.json`（不跟项目走）；两级合并、项目优先。
> 取值优先级：命令行参数 > 项目 `vague-code.json` > 全局配置文件 > 内置默认。

### 不用配置文件（每次敲参数）也行

```bash
# OpenRouter 示例（模型名带斜杠也支持）
vague-code tui --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY --model openai/gpt-4o

# Codex 中转站示例（实测通过）
vague-code tui --base-url https://code.newcli.com/codex/v1 --api-key-env RELAY_KEY --model gpt-5.6-sol
```

### 发布前先验证中转站协议

vague-code 默认使用 OpenAI **Chat Completions** 协议（`POST /v1/chat/completions`），
也支持 **Responses API**（`POST /v1/responses`，Codex CLI 用的协议，配置里加
`"protocol": "responses"`）。先确认中转站支持哪个：

```bash
# 测试 Chat Completions（Windows，%RELAY_KEY% 换成你的 key）
curl.exe https://中转站URL/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer %RELAY_KEY%" ^
  -d '{"model":"模型名","messages":[{"role":"user","content":"hi"}]}'

# 测试 Responses API
curl.exe https://中转站URL/v1/responses ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer %RELAY_KEY%" ^
  -d '{"model":"模型名","input":"hi"}'
```

- 哪个返回 JSON（含 `choices` 或 `output`）→ 用哪个协议（`protocol` 缺省即 Chat Completions）
- 两个都 404 → 该中转站不兼容，换一家

**常见坑**：① `base_url` 有的带 `/v1` 后缀有的不带，以服务商文档为准；② 模型名必须用服务商文档列的名字；③ 若被 Cloudflare 拦截（403 code 1010），检查是否有代理/防火墙限制，vague-code 本身不会被拦（实测正常）。

---

## 三种界面

| 界面 | 命令 | 说明 |
|---|---|---|
| **CLI**（单次任务） | `vague-code "任务"` | 跑完即退出，适合脚本化 |
| **TUI**（全屏交互） | `vague-code tui "任务"` | 多会话、流式渲染、权限对话框，日常推荐 |
| **chat**（REPL 对话） | `vague-code chat` | 终端内连续对话，`exit` 退出 |

> 从源码运行（开发者）：`uv sync` 后把命令中的 `vague-code` 换成 `python -m vague_code.cli`。

---

## TUI 模式

`vague-code tui [task]` 启动全屏交互式终端界面（基于 Textual 框架，v2 分层架构见 `docs/adr/0019-tui-v2-architecture.md`）。

### 布局
- **Topbar** — 运行状态 · provider/model · 权限模式 · 工作目录（窄屏自动截断）
- **Conversation View** — 流式 Markdown 渲染；消息类型左边线分色（用户=青 / 助手=绿 / 工具=灰 / 权限=琥珀 / 错误=红）；thinking 超长自动折叠（`T` 展开）
- **Activity Line** — 状态动画（`thinking [..] / running [=  ] / streaming [>  ]`）+ 回合指标（`12.3s · 2 tools`）
- **Composer** — 多行输入（`Shift+Enter` 换行），运行中发送进入 guidance 队列，下一轮生效

### 键绑定

| 键 | 操作 |
|-------|--------|
| `Enter` | 发送 |
| `Shift+Enter` | 换行（部分终端不支持时可用 `Ctrl+J`） |
| `↑` / `↓` | 输入历史（焦点在输入框时） |
| `Esc` | 聚焦输入框；**运行中按两次**（1 秒内）中断当前回合 |
| `Ctrl+C` | 有选中则复制；运行中则中断（**不退出**） |
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
| `/model` / `/model <name>` | picker（按当前 provider 分组）或直接切换模型 |
| `/mode <m>` | 设置权限模式 `safe\|normal\|autoedit\|auto` |
| `/permissions` | 列出持久化权限规则 |
| `exit` | 退出 TUI（唯一退出方式） |

### 权限
交互式确认对话框：`Y` 允许一次，`Ctrl+Y` 始终允许并持久化规则至 `.agent/permission-rules.json`，`N` 拒绝。
对 `write_file`/`patch` 会先展示**写入前 diff 预览**（红删绿增）；拒绝时可填理由，理由会回传给模型（反馈闭环）。

### CLI 常用参数

```
vague-code [task] [--model] [--provider {deepseek,openai,anthropic}]
           [--base-url URL] [--api-key-env NAME] [--max-turns N]
           [--mode {safe,normal,autoedit,auto}] [--db-path PATH]
           [--timeout-s N] [--no-repo-map] [--resume RUN_ID]
```

| 参数 | 说明 |
|---|---|
| `--provider` | deepseek / openai / anthropic（默认 deepseek） |
| `--base-url` | 覆盖端点（任意 OpenAI 兼容服务） |
| `--api-key-env` | 自定义 Key 的环境变量名 |
| `--model` | 任意模型名（OpenRouter 的 `provider/model` 斜杠格式也支持） |
| `--mode auto` | 无人值守：允许所有写操作与安全命令（危险 bash 仍需确认） |
| `--resume RUN_ID` | 断点续跑历史任务 |

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
│  Model Abstraction ──────┴─── Codecs (OpenAI / Anthropic) ─────→   │
│  Custom IR (text/thinking/tool_use/tool_result) + Unified Stream    │
├─────────────────────────────────────────────────────────────────────┤
│  Trajectory (Event-Sourced JSONL → SQLite)                          │
├─────────────────────────────────────────────────────────────────────┤
│  Repo Map (tree-sitter 符号索引) ── code_search 工具 + 地图注入     │
├─────────────────────────────────────────────────────────────────────┤
│  Eval Harness ── 20 题本机可跑 ── 真验收/pass^k ── Report           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 核心模块

| 模块 | 文件 | 描述 |
|------|------|------|
| **Agent Loop** | `vague_code/agent/loop.py` | ReAct 循环：LLM → tool_use → tool_result → LLM，含 retry/checkpoint/resume |
| **Tool System** | `vague_code/agent/tools.py` | 7 个工具（read/write/patch/glob/grep/bash/code_search），JSON Schema 校验，50K 截断 |
| **Concurrency** | `vague_code/agent/concurrency.py` | 冲突可串行化 ThreadPool 调度，资源 scope 提取 + 冲突检测 |
| **Context Engineering** | `vague_code/agent/context_compress.py` | 五层压缩：stale_snip → microcompact → structured_snip → auto_compact → truncation |
| **Permission System** | `vague_code/agent/permission.py` | 4 种模式（safe/normal/autoedit/auto）+ 30+ 类危险命令正则 |
| **Memory System** | `vague_code/agent/memory.py` | SQLite 统一记忆库 + episodic 检索注入 + auto-compact 蒸馏 |
| **Repo Map** | `vague_code/agent/repomap.py` | tree-sitter 符号索引，code_search 工具 + 符号地图注入 |
| **Model Abstraction** | `vague_code/agent/codecs/` | 自定义 IR → OpenAI(DeepSeek/GPT)/Anthropic codec，统一流式事件 |
| **Trajectory** | `vague_code/agent/trajectory.py` | Event-sourced JSONL → SQLite 事件流，to_messages() 导出 |
| **CLI** | `vague_code/cli/` | argparse + Rich 渲染，三入口（CLI/TUI/chat），多 provider 支持 |
| **Eval Harness** | `eval/` | 真验收/sanity gate + pass^k + Markdown 报告 |

---

## 评测

> ⚠️ 早期 v0.1 的 83%/93% 等数字基于**假 pass/fail**（验收测试未实跑），已废弃且不得引用。
> 2026-08 起评测体系按 `docs/plans/0016-eval-methods.md` 全面升级：真验收（sanity gate 双检 +
> F2P/P2P 实跑）、pass^k 可靠性、任务集按 OpenAI SWE-bench Verified 官方标注重建。

```bash
python -m eval.cli --tasks eval/tasks.json --fake          # 验证框架（不耗 API）
python -m eval.cli --tasks eval/tasks.json --max-turns 25  # 真实评测（20 题本机可跑）
python -m eval.cli --tasks eval/tasks.json --repeat 3 --out report.md  # pass^k 实验
```

现状：任务集 20 题本机可跑（sympy 17 + sphinx 2 + pytest 1）；8 题小消融显示基线全开 pass 最高
（5/8），压缩减少 29% 输入 token 但 KV Cache 命中率 93%→27%（详见 `docs/handoff/2026-08-05-vague-code-concurrency-and-ablation.md`）。

---

## 项目结构

```
vague-code/
├── vague_code/agent/                 # Agent 核心包
│   ├── loop.py                # ReAct 主循环
│   ├── tools.py               # 6 个基础工具 + code_search spec
│   ├── concurrency.py         # 冲突可串行化并发
│   ├── context_compress.py    # 五层压缩流水线
│   ├── context.py             # 系统提示构建
│   ├── context_tokens.py      # Token 计数 + 预算（按模型选词表/窗口）
│   ├── context_rules.py       # 规则文件层级加载
│   ├── permission.py          # 权限系统
│   ├── memory.py              # 记忆系统
│   ├── memory_tool.py         # memory_search 工具
│   ├── repomap.py             # tree-sitter 符号索引（code_search + 地图注入）
│   ├── config.py              # AgentConfig 配置
│   ├── ir.py                  # 自定义 IR dataclass
│   ├── backend.py             # LLM 后端适配层
│   └── codecs/                # 厂商 codec
│       ├── deepseek.py        # OpenAI 兼容 codec（DeepSeek / GPT / 中转站）
│       └── anthropic.py       # Anthropic codec
├── eval/                      # 评测框架
│   ├── cli.py / harness.py / env.py / verify.py / matrix.py
│   ├── reporter.py / metrics.py / judge.py / rubric.py / classify.py
│   ├── select_verified_tasks.py
│   └── tasks.json             # 20 题任务集（本机可跑）
├── vague_code/cli/            # CLI 薄壳（三入口 + provider 配置）
├── docs/                      # ADR / plans / articles / handoff / known-issues
└── tests/                     # 780+ 条自动化测试
```

---

## 技术栈

| 层 | 选型 |
|------|---------|
| 语言 | Python 3.12 |
| LLM API | DeepSeek / OpenAI（GPT 系列）/ Anthropic / 任意 OpenAI 兼容端点 |
| 记忆存储 | SQLite |
| 代码索引 | tree-sitter 0.26 + tree-sitter-python |
| 代码质量 | ruff + mypy + pytest（780+ tests） |
| CLI | argparse + Rich |
| TUI | Textual 8 |
| 依赖管理 | uv |

---

## 相关知识

- [Agent 架构决策目录](docs/adr/) — ADR，覆盖所有模块设计决策
- [完整实现计划](docs/plans/) — 逐步骤实现
- [已知问题](docs/known-issues.md) — 未修复的低优问题跟踪
- [评测工具链说明](eval/README.md)

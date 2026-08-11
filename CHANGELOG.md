# Changelog

## [0.1.14] — 2026-08-11

### Fixed

- **memory.py f-string 反斜杠（3.12 特有语法 PEP 701）**：requires-python 降 3.11 后 CI ruff 抓出——3.11 解释器会在导入时 SyntaxError；改为字符串拼接（ruff py310/311 目标全过，3.11 真正兼容）
- **两个 TUI flaky 测试 CI Linux 根治**：setup_wizard 轮询加 widget 就绪探针（screen 切换后 compose 未 mount 的 NoMatches）、permission 测试改 dialog.dismiss(Decision.ALLOW) 直驱（绕过按键/焦点时序）

## [0.1.13] — 2026-08-11

### Changed

- **README 安装方式改 uv 优先**：`uv tool install vague-code`（Python <3.11 时 uv 自动下载解释器，解决用户机器装不上问题），`pip install` 降为备选；技术栈表 Python ≥3.11；classifier 补 3.11；测试数更新 830+

## [0.1.12] — 2026-08-11

### Changed

- **Python 版本下限 3.12 → 3.11**：修复 `pip install vague-code` 在 Python 3.11 机器上失败（全部版本被 Requires-Python >=3.12 过滤）；代码语法与运行时 API 已验证 3.11 兼容（唯一 3.12 限制项 StrEnum 在 3.11 可用），全部依赖下限 ≤3.10

## [0.1.11] — 2026-08-11

### Added

- **评测体系重构：Aider Polyglot + Docker 容器化**（ADR-0040，参考 FirstCoder 测评审计报告方法论）：`vague-code benchmark` 无交互评测入口（bypass 权限 + 反作弊提示词 + 预算）；互斥失败分类学（env_broken/infra/f2p_p2p 分账）；双指标口径 pass@1/e2e/pass^k/pass@k（Aider 口径）+ 非官方榜声明；证据链三件套 config/lock/result + `--resume-fail` 定向恢复；cost/token 分位；CI eval smoke；WSL2 dockerd + vague-eval 镜像（6 语言工具链）；**实测 225 题 pass@1 = 100%（224/224，$13）**，e2e 99.56%——cpp/go/java/js/python/rust 全 100%；complex-numbers 数据集缺陷（官方答案同样编译失败）剔除分母；verifier 修复链：CMakeCache 污染/Boost 1.74 config 误判/node 12→20/gradlew CRLF/Gradle JVM 代理/js 只跑题目 spec/输出预算 64K（thinking 计入输出，32K 截断难题）
- **对抗注入评测**（ADR-0040 遗留收尾）：harness 接入 `task_type=adversarial`（合成仓库 + safe 权限 + permission_check 拦截判定），实测 5/5 注入全拦截（rm -rf/.env 读取/越权写/curl|sh/chmod -R 777）
- **judge 一致性审计数字**：SWE 20 样本 exact 55% / within-1 65%（代理人工分口径）

### Fixed

- **rust/pig-latin 输出预算截断**：`AgentConfig.max_output_tokens` 可配（默认 32K 不变）——thinking 计入输出，32K 会把 thinking 长的难题截断成 max_tokens 死循环；评测用 64K 后单题重跑 9 轮 PASS
- **两个 TUI flaky 测试**：SetupWizard 推屏轮询等待替代固定 pause；permission 测试直接驱动 dialog.on_key 绕过 focus 竞态（全量负载下 press("y") 偶发落空）
- 字节码（80 个 .pyc）移除 git 跟踪 + gitignore

## [Unreleased] — 2026-08-10

### Added

- **评测体系重构：Aider Polyglot + Docker 容器化**（ADR-0040，参考 FirstCoder 测评审计报告方法论）：`vague-code benchmark` 无交互评测入口（bypass 权限 + 反作弊提示词 + 预算）；互斥失败分类学（env_broken/infra/f2p_p2p 分账）；双指标口径 pass@1/e2e/pass^k/pass@k（Aider 口径）+ 非官方榜声明；证据链三件套 config/lock/result + `--resume-fail` 定向恢复；cost/token 分位；CI eval smoke；WSL2 dockerd + vague-eval 镜像（6 语言工具链）；**实测 225 题 pass@1 = 100%（224/224，$13）**，e2e 99.56%——cpp/go/java/js/python/rust 全 100%；complex-numbers 数据集缺陷（官方答案同样编译失败）剔除分母；verifier 修复链：CMakeCache 污染/Boost 1.74 config 误判/node 12→20/gradlew CRLF/Gradle JVM 代理/js 只跑题目 spec/输出预算 64K（thinking 计入输出，32K 截断难题）

### Changed

- **会话级模型隔离 + 跨 provider 切换**（ADR-0039，见下）

## [Unreleased] — 2026-08-10

### Added

- **会话级模型隔离 + 跨 provider 切换**（ADR-0039）：每个会话独立持有 provider/model/backend，会话 A deepseek / 会话 B openai 并行互不干扰；`/model` 切换只作用于当前会话，topbar 跟随当前会话显示；无参 `/model` picker 列出全部 provider 的模型（detail 标注服务商）；跨 provider 切换时目标无 API key → 弹 SetupWizard（预选目标 provider/模型，Esc/取消按钮可回退原模型，零改动）；wizard 完成同时写全局配置并切换当前会话；`Agent` 会话内换 backend 保留对话上下文；修复原隐患——跨 provider 改模型不再出现"模型名与端点不匹配"

### Changed

- **`/` 命令候选浮层**（ADR-0038，对齐 opencode/Claude Code）：输入框键入 `/` 弹出命令列表、前缀实时过滤、↑/↓ 高亮、Enter 无参执行/有参填入、Esc 收起；命令清单与 `/help` 共用单一事实源

## [Unreleased] — 2026-08-10

### Added

- **TUI 首次引导**（ADR-0037）：首次 `vague-code tui` 未配置 API key 时弹出 SetupWizard——选 provider（DeepSeek/OpenAI GPT/Anthropic/自定义中转）→ 填 key（自定义中转另填 baseUrl/模型名/协议）→ 【测试连接】真实验证 → 写入全局配置（`~/.config/vague-code/.env` + `config.json`）→ 直接使用；key 读取链扩展为 项目 .env → 全局 .env → 环境变量
- **模型目录更新至 2026-08 现行**（官方核实）：DeepSeek 仅 `deepseek-v4-flash`/`v4-pro`（1M）；Anthropic 换 `claude-fable-5`/`opus-5`/`sonnet-5`/`haiku-4-5`，默认端点改官方 `api.anthropic.com`，AnthropicBackend 默认模型 `claude-fable-5`；删除已退场的 deepseek-chat/reasoner、claude 4.x

## [Unreleased] — 2026-08-10

### Added

- **`/compact` 展示摘要**（ADR-0036，对齐 opencode）：`compact_chat()` 返回摘要文本，TUI 压缩完成后把 `[会话摘要]`（Pi 风格结构化摘要）作为对话消息展示在对话流中，不再只是一行 token 数字

## [Unreleased] — 2026-08-10

### Changed

- **缓存友好压缩链**（ADR-0035，对齐 Claude Code/Codex/opencode/Pi 业界做法）：改写闸门 `rewrite_threshold`(0.7) 替代 microcompact/structured 独立阈值——利用率 ≤70% 时完全不动历史（缓存前缀稳定、高命中），超阈值一次性执行全部改写型层（stale→micro→structured）后缓存重新积累；`auto_compact` 摘要升级为 Pi 风格结构化模板（Goal/Progress/Key Decisions/Next Steps/Critical Context + `<read-files>`/`<modified-files>` 文件追踪跨轮累积）

## [Unreleased] — 2026-08-10

### Added

- **GPT 系列 API 支持**（ADR-0032）：`--provider openai`（CLI/chat/TUI 三入口）+ `--base-url`/`--api-key-env` 覆盖任意 OpenAI 兼容端点（OpenRouter 等）；TUI `/model` 按 provider 分组；tokenizer 按模型切换（GPT 系列 → cl100k）；CONTEXT_WINDOWS 补 gpt-4o 128K / gpt-4.1 1M / o3-mini 200K；模型名校验放行 `provider/model` 斜杠格式；topbar 修复 provider 显示

### Changed

- **项目重命名为 vague-code**（ADR-0031）：PyPI 包名 `xcode` → `vague-code`、import 根 `src/` → `vague_code/`、console 脚本 `xcode` → `vague-code`、产品名 XClaw → vague-code（类标识符 XClaw* → VagueCode*）；PyPI 发布就绪——补全元数据（description/readme/license/authors/classifiers/keywords）、`package-data` 打包 `theme.tcss`、`vague_code/cli/__main__.py`、新增 publish workflow（tag 触发 + trusted publishing）
- 死代码清理（ADR-0030 Phase 3，对应 死代码.md A 类）：见 2026-08-10 Fixed 段

## [Unreleased] — 2026-08-10

### Fixed

- **chat 多轮用户消息落轨迹**（ADR-0030）：新增 `EventType.user_message`，`chat()` 后续轮 emit 事件、`to_messages()` 消费——`chat_resume`/轨迹重放/LLM-as-Judge 不再丢失第 2+ 轮用户消息，也不再产生连续 assistant 消息（角色交替违例）
- **chat 悬挂 tool_use 补执行**：中断/崩溃于工具执行中后，续聊与 `chat_resume` 复用 `_execute_pending_tools` 补执行并回填结果（新文本合并进结果消息），不再把无结果的 tool_calls 发给 API
- **max_tokens 终止同步部分回复**：chat 模式下截断的 assistant 消息进入会话上下文，可继续对话
- **侧边栏历史会话续聊接续原 run**：`_switch_session` 对 DB chat 会话接线 agent + `resume_run_id`，后续输入走 `chat_resume` 而非开启全新 run
- **TUI worker 竞态与键泄漏**：`_session_workers` 在 rename 时 remap 旧键；中断后旧 worker 线程未退出时提交消息入队而非开第二个 worker，worker 退出后自动开始排队轮

### Added

- **CLI 权限可用**（S1）：`vague-code`/`vague-code chat`/`vague-code tui` 增加 `--mode {safe,normal,autoedit,auto}`，并自动加载工作区 `.agent/permission-rules.json`（`vague-code "Fix..." --mode auto` 可无人值守编辑）
- **危险命令模式补盲**（M5）：`git reset --hard`/`git clean`/`git checkout --`/`git restore`/`pip(p3) install`/`npm install`/`yarn add`/`taskkill`/`format X:` 判为 dangerous

### Removed

- 死代码清理（ADR-0030 Phase 3，对应 死代码.md A 类）：`retry_divergence`/`mode_change` 枚举、`MemoryConfig.search_top_k`、`RepoMapConfig.languages`+`RepoIndex.languages`、`_StreamAggregator._result`、`MemoryStore._db_path`/`recent()`/`ingest(confidence)`、`XClawApp._pending_guidance`、`ConversationView._pinned`、`_begin_new_session(first_text)`、`SessionSidebar.set_current`、`JudgeResult.raw_output`、`EnvSpec.repo_key`、`select_tasks(output_dir)`、`load_gold()`（+judge.py import）、`LayerReport.unit`、harness `_FakeBackend.call_count`

## [Unreleased] — 2026-08-07

### Added

- **10 题全量基线 + 消融实验完成**（78 runs，$25.08）：核心层 10 实例 × k3 pass^3 达标 **8/10（80%）**；消融层 8 实例 × 3 单变量关闭配置 × k2——关 RepoMap 零损失（16/16）、关压缩 15/16、关并发 14/16，三变量整体无显著消融效应（损失集中在 21612 单题）
- **监督全量复验**（ADR-0020 标准 4/5）：stagnant 1.3%、监督增量 6.8%，双达标
- **压缩结论定案**（87 runs 累计）：40 轮任务仅触发 stale_snip，microcompact/structured_snip/auto_compact/truncate 零触发，五层流水线后半段在短任务集无收益

### Changed

- 评测报告链：`runs/eval/results_20260807-{135536,161222,162351}.json` + `b10_p{1,2,3}.md` + `b10_baseline_report.md`；handoff `docs/handoff/2026-08-07-vague-code-baseline-complete.md`

## [Unreleased] — 2026-08-04

### Added

- **TUI v2 整体重写**（ADR-0019，参考包分层架构）：Topbar / Welcome / Activity Line / Composer 新布局；流式 Markdown 渲染（0.2s 节流 + 更新防乱序 + 流式禁选）；工具活动流（running→success/error + 耗时 + 并行计数）；thinking 自动折叠；命令系统（`CompositeCommandHandler` + `/resume /model /mode /permissions /new /save`）；picker 选择器；输入历史（↑/↓）；Esc 两次中断；运行中 guidance 消息；**写入前 diff 审查 + 拒绝理由反馈闭环**；resume 轨迹重放；`TuiTranscript` 单一事实源 + views 纯函数渲染层（可单测）
- **Agent 层**：`on_tool_result` 带 tool id；`Operation.review`（prewrite diff）/ `Operation.feedback`；`guidance_provider` 回合头注入

### Changed

- **TUI**：侧边栏/visitor/status_bar 等旧实现删除（`visitor.py`、`widgets/sidebar.py`、`screens/session_detail.py` 等）；`vague_code/tui` 测试从 6 个增至 74 个

## [Unreleased] — 2026-08-01

### Added

- **Repo Map**: tree-sitter 0.26 symbol index (`repomap.py`), `code_search` tool + system prompt map injection (max 1000 tokens), mtime incremental refresh
- **structured_snip**: trajectory-driven compression layer (zero LLM cost) between microcompact and auto_compact — replaces completed read→modify→verify subtasks with structured summaries

### Changed

- **Compression Pipeline**: 4 → 5 layers (stale_snip → microcompact → structured_snip → auto_compact → truncation)
- **Memory System**: removed pinned (always-on) injection — `get_pinned()`/`inject_pinned` deleted; constant knowledge handled by `.agent/rules.md` (ADR-0008). `kind` column retains only `'episodic'`
- **Evaluation Harness**: matrix expanded to 2×2×2 (compression × concurrency × repo_map) × repeats; stats track `structured_snip_reclaimed` and `code_search_calls`

## [v0.1.0] — 2026-07-28

### Added

- **Agent Runtime**: ReAct loop (LLM → tool_use → tool_result → LLM) with retry, checkpoint/resume, streaming
- **Tool System**: 6 tools (read_file/write_file/patch/glob/grep/bash) with JSON Schema, path traversal protection, conflict-serializable concurrent scheduling
- **Context Engineering**: 4-layer compression pipeline (stale_snip → microcompact → auto_compact → truncation)
- **Permission System**: 4 modes (safe/normal/autoedit/auto) with rule-based overrides, dangerous command classification, audit log
- **Memory System**: SQLite FTS5 unified store, pinned (always-on) and episodic (on-demand) injection, SHA-256 deduplication, time-decayed recall scoring
- **TUI (Textual)**: Full-screen interactive interface — streaming conversation, collapsible thinking/tool-result blocks, modal permission dialog, session sidebar with resume, status bar, slash commands, dark theme
- **Evaluation Harness**: SWE-bench ablation matrix (2×2 compression × concurrency × 3 repeats), Markdown report generator, fake/reel backends
- **Model Abstraction**: Custom IR with text/thinking/tool_use/tool_result block types, codecs for DeepSeek (OpenAI-compatible) and Anthropic, unified stream event protocol
- **Trajectory**: Event-sourced JSONL → SQLite persistence, to_messages() export for LLM-as-Judge

### Fixed (this session)

- 11 known-issue fixes across concurrency, permission, compression, memory, trajectory
- 10 TUI stress-test bugs (resume thread, sidebar leak, timeout, tool args display, etc.)
- 2 compression bugs (microcompact char-based fallback, ThinkingBlock summary inclusion)
- 1 stale_snip detection bug (_READ_TOOLS missing "read_file")
- 6 architecture concerns (silent pass, permission dedup, mypy/ruff zero errors)

### Infrastructure

- **CI/CD**: GitHub Actions (ruff + mypy + pytest on push/PR to main)
- **Testing**: 477 tests passing, 28 TUI widget tests
- **Dependencies**: uv, Python 3.12, 7 runtime + 5 dev packages
- **Codebase**: 34 source files, ~4,500 lines src, ~3,500 lines test

### Known issues

- `truncate` greedy timing: correct but suboptimal under tight budget (high cost to rewrite)
- `resume` duplicate compression events: analysis-side dedup by `(run_id, turn, layer)`

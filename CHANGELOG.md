# Changelog

## [Unreleased] — 2026-08-04

### Added

- **TUI v2 整体重写**（ADR-0019，参考包分层架构）：Topbar / Welcome / Activity Line / Composer 新布局；流式 Markdown 渲染（0.2s 节流 + 更新防乱序 + 流式禁选）；工具活动流（running→success/error + 耗时 + 并行计数）；thinking 自动折叠；命令系统（`CompositeCommandHandler` + `/resume /model /mode /permissions /new /save`）；picker 选择器；输入历史（↑/↓）；Esc 两次中断；运行中 guidance 消息；**写入前 diff 审查 + 拒绝理由反馈闭环**；resume 轨迹重放；`TuiTranscript` 单一事实源 + views 纯函数渲染层（可单测）
- **Agent 层**：`on_tool_result` 带 tool id；`Operation.review`（prewrite diff）/ `Operation.feedback`；`guidance_provider` 回合头注入

### Changed

- **TUI**：侧边栏/visitor/status_bar 等旧实现删除（`visitor.py`、`widgets/sidebar.py`、`screens/session_detail.py` 等）；`src/tui` 测试从 6 个增至 74 个

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

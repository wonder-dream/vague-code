# Changelog

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

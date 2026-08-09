# 0022: CLI/TUI max_turns 默认值修复（500 未生效问题）

- **日期**: 2026-08-09
- **状态**: approved（方案 B）

## 问题

用户设置/期望 max_turns=500（`AgentConfig` 默认，config.py:100），但 `xcode <task>` 与 `xcode tui` 入口的 argparse `--max-turns` 默认 20（src/cli/__init__.py:33、:141）覆盖了配置默认，导致未传参时 20 轮即熔断。

实测（runs.db `a7c43eaa5a33`）：20 个 turn_start 后 `run_end {reason: "max_turns", pending_tool_calls: 2}`（熔断点 loop.py:484）。

eval 入口默认已是 500（eval/cli.py:42），不受影响。

## 修复（方案 B）

- `src/cli/__init__.py` 两处 `--max-turns` argparse `default=20` → `default=None`
- 构造 AgentConfig 时：`max_turns=args.max_turns if args.max_turns is not None else AgentConfig.max_turns`（未指定则用配置类默认 500）

## 验证

- `pytest tests/test_cli.py tests/test_cli_process.py -q` 全过
- `xcode "task"`（不传参）构造的 config.max_turns == 500
- `xcode "task" --max-turns 30` 构造的 config.max_turns == 30
- ruff check

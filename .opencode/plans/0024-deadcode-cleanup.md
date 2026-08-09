# 0024: 死代码清理（综合双审查结果，Tier 1 + Tier 2）

- **日期**: 2026-08-09
- **状态**: approved（用户确认 Tier 1 + Tier 2，Tier 3 保留）

## 背景

综合本会话人工审查与 xcode 生成的 `xcode审查结果.md` 双方结果，清理确认死代码。范围：Tier 1（零风险）+ Tier 2（生产函数连带测试删除）。

## Tier 1 — 零风险删除

**产品代码 5 处**
1. `src/tui/commands/core.py:39` `CompositeCommandHandler.register`（xcode）
2. `src/tui/widgets/conversation.py:36` `ConversationView.render_transcript`（双方）
3. `src/tui/picker.py:38` `TuiPickerState.selected_item`（本会话）
4. `src/tui/mixin.py:83` `_stream_reasoning_started`（本会话）
5. `src/tui/mixin.py:80,377` `_stream_finalizations` + `finalize()` completion 机制简化（本会话）

**eval 2 处**
6. `eval/env.py:140` `EnvSpec.pip` property（xcode）
7. `eval/metrics.py:236` `aggregate`（双方）

**测试代码 6 处**（删除不影响任何测试运行）
8. `tests/test_eval_verify.py:211` `_BatchProc`
9. `tests/test_context_rules.py:7` `_RULES_FILENAME`
10. `tests/test_cli.py:63,147,236,373` 4×`_fix_env`

**遗留临时脚本 3 个**
11. 根目录 `scratch_deadcode.py`、`scratch_refs.py`
12. `scripts/_deadcode_scan.py`

**其他**：`src/tui/**/__pycache__` 旧 v1 pyc 残留

## Tier 2 — 连带删除测试

| 符号 | 连带测试 |
|------|----------|
| `src/agent/retry.py:146` `response_signature` | test_retry.py 响应签名测试段 |
| `src/tui/views/activity.py:49` `compact_tool_arguments` | test_views.py `test_compact_tool_arguments` |
| `eval/classify.py:85,69` `write_chart`/`render_chart` | test_eval_classify.py `test_write_chart` |
| `eval/metrics.py:202` `trajectory_grade` | test_eval_metrics.py 4 个测试 |
| `src/tui/app.py:607` `_has_pending_guidance` | test_commands.py:195 断言行改写 |

## Tier 3 — 保留（观察项）

`scripts/demo_e2e.py`、`scripts/verify_anthropic.py`、`scripts/wsl_*.sh`、`StreamEventVisitor`、`eval/select_tasks.py`（有 `__main__`）、eval 历史报告 12 个。

## 验证

1. `pytest tests -q` 全量 0 回归
2. `ruff check src eval tests` + `mypy src`
3. git status 核对删除清单

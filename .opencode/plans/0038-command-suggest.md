# 0038: 输入 / 弹出命令候选浮层（对齐 opencode / Claude Code）

- **日期**: 2026-08-10
- **状态**: approved（用户确认）

## 交互

```
键入 "/" → 输入框上方弹出命令列表；继续输入前缀过滤（/mo → /model /mode）
↑/↓ 移动高亮；Enter：无参命令直接执行、有参命令填入"命令+空格"继续输参数
（输入已含参数如 "/model gpt-5.6" 时 Enter 放行正常提交）；Esc 关闭；非 / 隐藏
```

## 改动

1. `tui/commands/handlers.py`：新增 `COMMAND_LIST`（命令/描述/需参数），
   `_HELP_TEXT` 由它生成（DRY）
2. `tui/widgets/command_suggest.py`（新）：CommandSuggest 浮层（候选行+高亮，
   默认 display:none），挂 composer 输入框上方
3. `tui/app.py`：compose 挂载；`TextArea.Changed` 计算候选（纯函数
   `filter_commands` 前缀匹配）；on_key 浮层显示时拦截 ↑/↓/Enter/Esc
4. `theme.tcss`：浮层样式
5. 测试：filter_commands 单测 + TUI 交互测试（弹出/过滤/Enter 语义/Esc/隐藏）

## 验证

全量 pytest + ruff/mypy + 提交发布 v0.1.10。

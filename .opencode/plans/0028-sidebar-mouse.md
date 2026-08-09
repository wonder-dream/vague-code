# 0028: 侧边栏鼠标支持（单击切换 + 悬停高亮）

- **日期**: 2026-08-09
- **状态**: approved（用户确认：单击即切换、加悬停高亮）

## 改动

- `src/tui/widgets/sidebar.py`：行 Static 挂 `run_id` 属性 + `on_click`（设置 selected_index + 重绘 + post SessionSelected）
- `src/tui/theme.tcss`：`#session-rows > Static:hover` 悬停高亮

## 行为

单击行 = 选中并切换会话（复用现有切换通道）；滚轮原生支持；键盘/删除确认保留。

## 测试

pilot.click 点击行 → 会话切换 + 选中态；TUI 全量回归。

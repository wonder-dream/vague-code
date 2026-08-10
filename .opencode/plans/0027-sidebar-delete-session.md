# 0027: 侧边栏删除会话（物理删除 + 确认弹窗）

- **日期**: 2026-08-09
- **状态**: approved（用户确认：物理删除、二级确认复用弹窗、运行中禁止删除）

## 决策

1. **物理删除**：DB `DELETE FROM runs` + `DELETE FROM events`（单事务）+ 内存 SessionManager 移除
2. **二级确认**：复用 ModalScreen 模式的新 `ConfirmDialog`（y/enter 确认，n/Esc 取消）
3. **运行中禁止删除**：busy 会话按 `d` 不弹窗，提示"先中断再删除"

## 改动

- `vague_code/tui/screens/confirm.py`（新）：`ConfirmDialog(title, message) -> ModalScreen[bool]`
- `vague_code/tui/session.py`：`SessionManager.remove(run_id) -> bool`（是否删除当前）
- `vague_code/tui/app.py`：
  - `_prompt_delete_session()`（busy 检查 + push ConfirmDialog）
  - `_delete_session(run_id)`（DB 事务 + 内存移除 + 删除当前则切换到剩余第一个/欢迎页）
  - on_key 侧边栏焦点 `d` 分支
- `vague_code/tui/theme.tcss`：ConfirmDialog 样式

## 测试

- 删除流程（弹窗→y→DB/内存移除→列表刷新）、取消（n/Esc）、busy 禁删、删除当前切换/欢迎页

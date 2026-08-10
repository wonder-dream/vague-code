# 0021: TUI 审查修复 — 启动崩溃、交互 bug 与信息补全

- **日期**: 2026-08-09
- **状态**: approved（P0+P1+补测试，含信息补全）

## 背景

用户反馈"TUI 还是不行"。审查（headless Pilot + fake agent 实测）发现：

- **P0** `CSS_PATH = "theme.tcss"`（`vague_code/tui/app.py:55`）按 cwd 解析，从项目根运行 `vague-code tui` 直接 `StylesheetError` 崩溃。现有测试用绝对路径 `_TUI_THEME` 覆盖 CSS_PATH（`tests/tui/test_streaming.py:21` 等 5 处），掩盖了该 bug。
- **P1** composer 无初始焦点（焦点落在 output，输入无效）；权限对话框焦点落在 Deny feedback 输入框，y/n/Esc 键盘失效；picker 打开后 Enter 无法选择（参考包在 `on_composer_text_area_submitted` 处理，vague-code 缺失）。
- **信息不完整** reasoning 条目 status 卡在 "running"；压缩回收量 `_total_reclaimed` 只累计不展示。
- **测试盲区** 现有 App 级测试全部直接调 `_submit_task()`/`_on_stream_event()`，绕过 composer/键盘/worker 线程，上述 bug 全部漏网。

## 修复项

### 1. P0 — CSS_PATH 相对路径

`vague_code/tui/app.py:55` 改为：

```python
CSS_PATH = Path(__file__).parent / "theme.tcss"
```

tests/tui 下 5 个文件的 `_TUI_THEME` 绝对路径覆盖改为不再覆盖（防回归掩盖），新增 CSS 解析冒烟测试。

### 2. P1 — composer 初始焦点

`vague_code/tui/app.py` `on_mount` 末尾 `self.query_one("#input").focus()`。

### 3. P1 — picker Enter 选择

`vague_code/tui/app.py` `on_composer_text_area_submitted`：picker 打开时 Enter → 输入为合法数字则 `_picker_select_number`，否则选中高亮项（`_picker_select_index(picker.selected_index)`）。

### 4. P1 — 权限对话框键盘

`vague_code/tui/screens/permission.py`：`on_mount` 焦点给 `#perm-allow` 按钮，y/n/Esc/反馈输入全部可键盘操作。

### 5. 信息补全

- `app.py _finalize_reasoning`：未折叠的 reasoning 条目 status 置 None（不再显示 running）。
- `_on_run_complete`：有压缩回收（`_total_reclaimed > 0`）时输出 system 提示（"已回收 N tokens"）。

## 补测试：`tests/tui/test_app_interaction.py`

Pilot 真实键盘路径 + stub worker（复用 test_streaming.py 模式）：

1. mount 后焦点在 composer；按字母+Enter 提交任务（启动前必挂）
2. `/resume` 打开 picker → Enter 选中（启动前必挂）、数字选择、Esc 取消
3. 权限对话框打开时焦点在 Allow 按钮，按 y → ALLOW、Esc → DENY
4. CSS_PATH 解析冒烟（任意 cwd 可启动）

## 验证

- `pytest tests/tui -q`：现有 90 + 新增全过
- `ruff check vague_code/tui tests/tui`、`mypy vague_code/tui`
- headless run_test 从项目根 cwd 启动无异常

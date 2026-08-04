# ADR-0019: TUI v2 分层重写（参考包架构 × 同步 Agent）

- **日期**: 2026-08-04
- **状态**: accepted
- **取代范围**: 0015 保留（Textual 选型与线程桥接决策不变），本 ADR 记录 UI 层架构与交互模型的重写

## 背景

v1 TUI（ADR-0015）是"能用但薄"的 MVP：视觉简陋（GitHub Dark 平铺）、交互薄弱（无 Markdown 渲染、无工具活动流、无命令系统、无输入历史/Esc 中断、无写入审查、resume 不重放历史），且结构脆弱（`app.py` 363 行混三层职责；tool call 走 StreamEvent、tool result 走回调两条通道不对称且无 tool id 关联；流式渲染每 delta 全量字符串拼接 O(n²)）。

## 决策

基于 `tui-reference-pack/`（firstcoder 项目的成熟 Textual Coding Agent TUI，约 3900 行 / 30 文件 / 267 测试）整体重写 UI 层。**UI 层设计照搬参考包，适配层自研**（XClaw 的 Agent 是同步生成器 + 阻塞回调，参考包是 asyncio AgentLoop，桥接方式不同）。

### 1. 新分层

```
app.py          薄壳：compose/bindings/事件分发/回合管理（v1 的 363 行拆解）
runner.py       XClawAgentRunner：同步 Agent ↔ 异步 UI 桥（事件回调/取消/guidance/规则/resume）
mixin.py        XClawViewMixin：流式三层缓冲 + 活动动画 + 回合 metrics
state.py        TuiTranscript：展示态单一事实源（entries 带 widget 引用）
views/          纯函数渲染（topbar/activity/welcome/transcript/review），可独立单测
commands/       CompositeCommandHandler + CommandResult(handled, output, action)
widgets/        ConversationView / ActivityLine / ComposerTextArea / XClawMarkdown
screens/        PermissionDialog（diff 预览 + 拒绝理由）
```

### 2. 交互模型（与 v1 的差异）

| 项 | v1 | v2 |
|----|----|----|
| 布局 | 左侧边栏 25% + 对话 75% + 状态栏 | Topbar + 对话 + Activity Line + Composer（输出区最大化） |
| 会话管理 | 侧边栏点击 → SessionDetail screen | `/resume` picker（选中后重路由命令） |
| 流式渲染 | 每 delta 全量 `str+delta` + update（O(n²)） | 0.2s 定时 flush + future guard 防乱序覆盖 + 流式期间禁选 |
| 工具流 | 只有结果一个信息点 | 完整生命周期（running→success/error + 耗时 + 参数/结果压缩 + 并行计数） |
| 运行中输入 | 拒绝 | guidance 队列，回合头注入为 user 消息 |
| 中断 | Ctrl+C 直接停 | Esc 两次（1 秒窗口）；Ctrl+C 复制→中断→退出 |
| 权限 | ALLOW/DENY 二元 | prewrite diff 预览 + `reject: 理由` 反馈闭环（`Operation.review/feedback`） |
| 命令 | `_handle_slash` if/elif 链 | `CompositeCommandHandler` 前缀路由，命令即唯一事实源 |
| resume | 清空对话静默跑 | `Trajectory.from_db` 重放历史后再静默恢复 |
| 事件通道 | visitor 分发 + 回调不对称 | 全部经 `XClawAgentRunner` 回调直达 transcript；`on_tool_result` 带 tool id |

### 3. 为 UI 服务的 agent 层小改（4 处，最小侵入）

1. `on_tool_result(tool_id, tool_name, content, is_error)`——tool id 关联工具条目（仅 TUI 使用该回调）
2. `Operation.review`（`src/agent/prewrite.py` 计算的写入前 diff）+ `Operation.feedback`（拒绝理由并入返回模型的错误消息）
3. `Agent.guidance_provider`——回合头 drain 用户消息队列
4. `on_state_change` compression payload 供上下文可见性

### 4. 删除

`visitor.py`、`widgets/sidebar.py`、`widgets/status_bar.py`、`widgets/command_input.py`、`screens/session_detail.py`、`screens/help.py`。

## 后果

**正面**：可测试性大幅提升（views/commands/transcript 纯逻辑单测 + App 级 fake runner 测试，tests/tui 74 用例）；视觉与交互对齐主流 Coding Agent TUI；resume 免费获得历史重放。

**代价**：TUI 测试需真实时钟等待 flush timer（~0.2s/用例）；`on_tool_result` 签名变更影响所有调用点（已确认仅 TUI）；流式 finalize 依赖 Textual `Markdown.update` 的 `_future` 内部 API（textual 8.x 已验证）。

**范围外**：附件粘贴（Agent 仅接受任务文本，需先定义附件语义）；task plan 面板（XClaw 无任务计划模型）。

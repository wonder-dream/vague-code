# 0017: TUI 整体重写（v2 = 参考包分层 × 同步 Agent）

基于 `tui-reference-pack/`（firstcoder 项目的 Textual TUI）整体重写现有 `src/tui/`：当前 TUI 视觉简陋、交互薄弱，重写为参考包的分层架构 + 交互模型，适配层自研（XClaw 的 Agent 是同步生成器 + 线程回调）。

---

## 背景与动机

### 现状问题

1. **视觉简陋**——GitHub Dark 平铺配色，无 topbar / welcome / 活动行 / 消息类型色线，输出区文字堆叠
2. **交互薄弱**——无 Markdown 渲染、无工具活动流（工具只有"结果"一个信息点）、无输入历史 / Esc 中断 / 附件 / 命令系统（`_handle_slash` if/elif 链）、无上下文可见性、无写入审查、resume 不重放历史
3. **结构脆弱**——`app.py`（363 行）混三层职责；事件通道不对称（tool call 走 StreamEvent、tool result 走回调且无 tool id 关联）；流式渲染每 delta 全量 `str+delta` 拼接（O(n²)）；无 App 级测试

### 决策来源

1. **参考包已验证**：`tui-reference-pack/firstcoder/app/`（约 3900 行 / 30 文件）是同类产品（Textual Coding Agent TUI）的成熟实现，含 267 个测试。UI 层设计（transcript 单一事实源、views 纯函数、命令路由、picker、流式三层缓冲、Markdown 增量渲染）直接可移植
2. **差异化在适配层**：参考包的 `AgentChatRunner` 是 asyncio AgentLoop 封装；XClaw 的 `Agent.start()` 是同步生成器 + 阻塞回调（`_on_permission` / `on_tool_result` / `on_state_change`），桥接方式不同，需自研 `XClawAgentRunner`
3. **技术选型**：textual 8.x（现有依赖）；组件移植前先做 spike 验证（最大技术风险是 `FirstCoderMarkdown` 对 textual 8.x 的兼容性，通不过则降级 Static + 节流）

---

## 已确认决策

1. **去掉侧边栏** → `/resume` picker（参考包风格，输出区最大化）；会话列表查询抽到 `session_lib.py` 供 picker/重放共用
2. **运行中可发消息** → guidance 队列（agent 小改③），支持持续对话
3. **视觉采用参考包主题**（`#0f1014` 深色底 + 绿/青/琥珀/红分类色 + 消息左边线）

---

## 核心设计

### 1. 新目录结构

```
src/tui/
├── __init__.py        # main()：task 有值自动开跑，无值显示 welcome
├── app.py             # XClawApp 薄壳：compose / bindings / actions / worker 编排
├── runner.py          # ★ XClawAgentRunner：同步 Agent → 异步 UI 桥（新核心）
├── state.py           # TuiTranscript / TuiEntryKind / TuiEntry（移植 tui_state.py）
├── widgets.py         # FirstCoderMarkdown / ComposerTextArea / _observe_markdown_update（移植）
├── views/             # 纯函数渲染（移植，可单测）
│   ├── topbar.py  activity.py  transcript.py  welcome.py  permission.py  review.py
├── commands/          # 命令路由（移植 router 模式）
│   ├── router.py  session.py  model.py  context.py  permission.py  help.py
├── session_lib.py     # runs 表查询 + MemoryStore 封装（从旧 sidebar 抽出）
├── widgets/
│   ├── conversation.py   # 重写：transcript 驱动 + Markdown + 折叠块 + pinned 滚动
│   └── status.py         # 重写：activity 动画 + 回合 metrics
├── screens/permission.py # 重写：diff 审查 + 拒绝理由；help.py 重写
└── theme.tcss         # 重写
```

**删除**：`sidebar.py`、`session_detail.py`、`command_input.py`、`visitor.py`、旧 `conversation.py` / `status_bar.py` / `theme.tcss` 实现。

### 2. 适配层 `runner.py`（与参考包的差异点）

- `run_task(text)`：`@work(thread=True)` 跑 `agent.start()`，迭代器逐个事件经 `call_from_thread` 入 transcript（turn token 过滤 + 锁 + `call_later` 批量排空，照抄参考三层缓冲）
- 权限暂停/恢复：保留现有阻塞式 `_thread_permission` 桥（弹窗 await 后返回 Decision），runner 提供 `pending_operation` 快照供弹窗渲染
- 取消：Esc 两次（1s 窗口）→ `handle.close()` + worker.cancel（照抄 `_interrupt_chat_turn`）
- 运行中发消息 → guidance 队列（`add_guidance` / `drain_guidance`），agent 小改③消费
- resume：`Trajectory.from_db` 重放渲染 + `agent.resume()` 静默跑

### 3. 功能清单（v1）

- **视觉**：topbar（brand·状态·model/mode·cwd）、ASCII welcome + 粒子（≤80×24 降级）、消息类型色线
- **渲染**：流式 Markdown（0.2s flush + future guard + 流式禁选/finalize 放开）、折叠 thinking/tool 块（保留 T/E/Tab）、工具活动流（running→success/error + 耗时 + 参数/结果压缩）
- **交互**：输入历史 ↑/↓、Esc 两次中断、运行中 guidance、picker（`/resume` `/model`）、附件粘贴（可选 M6）
- **命令**：`/resume /new /model /mode /permissions /context /compact /save /clear /help /quit`
- **权限**：prewrite diff（红删绿增 + `review all/path/clear`）+ 拒绝理由回传模型（反馈闭环）
- **状态**：activity 行动画 + `12.3s · 2 tools` 指标 + 上下文预算
- **会话**：resume 前从 `Trajectory.from_db` 重放对话历史

### 4. Agent 层小改（4 处，均已定位）

1. `_fire_on_tool_result` + `block.id` 参数（`loop.py:572`，5 处调用点；已验证仅 TUI 使用该回调）→ 工具状态可关联
2. `permission.py` `Operation` + `review: dict|None`（prewrite diff）与 `feedback: str|None`；`_check_tool_permission`（`loop.py:516`）对 write/edit 工具执行前算 diff 挂到 `op.review`，拒绝后把 `op.feedback` 并入错误消息（`loop.py:553`）→ 审查 + 反馈闭环
3. guidance 钩子：每轮循环头 `drain_guidance()` 插队为系统消息 → 运行中可发消息
4. `on_state_change` 补 context budget payload → topbar 上下文预算显示

---

## 执行顺序（每步可交付可测）

- **M1 骨架 + spike**：新目录、theme.tcss、topbar/welcome/composer、state.py；先验证 FirstCoderMarkdown 在 textual 8.x 可移植（最大技术风险）
- **M2 事件流**：runner.py + 流式 Markdown 三层缓冲 + transcript 渲染（agent 小改①）
- **M3 工具活动流 + metrics**
- **M4 命令系统**：router + 新命令 + picker + 输入历史 + Esc 中断 + guidance（agent 小改③）
- **M5 权限审查**：diff + feedback（agent 小改②）
- **M6 会话重放 + 附件 + App 级测试**（fake runner），删旧文件

## 测试策略

- views 纯函数单测（topbar/activity/review/transcript 渲染）
- transcript 状态机单测（entry 增改、widget 关联、picker 原地更新）
- runner 用 fake agent 测（事件路由、turn token 过滤、取消、guidance）
- App 级测试：Textual Pilot + fake runner（参考包 `test_app_tui.py` 模式）

## 兼容性

- `xcode tui <task>` 入口不变；有 task 自动开跑，无 task 显示 welcome 待输入
- `src/cli`（Rich 模式）与 eval 工具链零改动（`on_tool_result` 签名改动仅影响 TUI）
- 权限规则文件（`.agent/permission-rules.json`）格式不变
- 参考包 textual 不锁版本，XClaw 为 `>=8.0.0`；M1 spike 先行验证，通不过则降级 Static + 节流

# 0026: TUI 并行会话侧边栏（真并行双活）

- **日期**: 2026-08-09
- **状态**: approved（grill-me 决策树全部确认）

## 已确认决策（grill-me 汇总）

| # | 决策 |
|---|------|
| Q1 | 侧边栏可折叠（`Ctrl+B` 切换 / `Esc` 收起），**默认收起** |
| Q2 | 只列会话（`mode=chat`），普通任务 run 不进侧边栏（仍走 `/resume`） |
| Q3/4 | **真并行双活**：切走继续跑，多会话可同时执行 |
| Q5 | topbar 聚合计数（`N running`）+ 侧边栏状态点（`●`运行中/`·`空闲）+ 活动行当前会话状态 |
| Q6 | 权限弹窗**排队** + 弹窗 header 标会话 |
| Q7 | `/new` = 并行开新会话并切换（旧会话不动）；会话无"结束"概念（不 emit `run_end`）；列表上限 20 |
| Q8 | 切换会话时运行中会话**继续跑**（输出写入其 transcript） |
| Q9/10 | 会话标题 = LLM 摘要（第一轮结束后生成一次，≤15 字，失败回退首条消息截断）；存 `runs.title` |
| Q11 | 一行一列表单，标题 `…` 省略；固定宽 28 列；无窄屏强制收起 |

## 架构改动

### 1. 会话管理器（新 `vague_code/tui/session.py`）
- `SessionState`：run_id / title / agent / transcript / worker / busy / pending_guidance
- `SessionManager`：`sessions: dict[run_id, SessionState]`、当前会话、新建/切换/查询

### 2. transcript 数据/视图分离（核心）
- `self.transcript` 全局单例 → 每会话 `SessionState.transcript`
- `_write_line`/流式缓冲按当前会话路由；非当前会话事件只写 transcript 不碰 widget
- `ConversationView` 恢复按 transcript 重建能力（`render_transcript` 复活）
- 事件回调 token 过滤 → `(run_id, token)` 双键

### 3. worker 并行化
- 去 `exclusive=True`（3 处），worker 命名 `agent-{run_id}`
- 中断（Esc×2）只中断当前会话 worker

### 4. 权限排队
- 全局队列；`_thread_permission` 等待位（120s 超时兜底）
- `PermissionDialog` header 加会话标识

### 5. 标题摘要
- `runs` 表加 `title` 列（启动幂等 `ALTER TABLE` 迁移）
- agent 层 `summarize()` 短调用；触发：第一轮 `end_turn` 后；失败回退 `task[:20]`

### 6. 数据/状态
- `Trajectory.persist` 加 `timeout=5`（并行落盘）
- guidance 按会话队列
- 侧边栏事件驱动刷新（无定时轮询）
- topbar 加 `N running` + 当前会话标题

### 7. 键位
`Ctrl+B` 折叠 / `↑↓`+回车 切换 / `n` 新建 / `Esc` 收起 / `Esc`×2 中断当前会话

## 实施顺序（每步可验证）
1. 会话管理器 + transcript 隔离（单会话行为不变回归基线）
2. worker 并行化 + 双键路由
3. 侧边栏 UI
4. 切换与恢复
5. 权限排队 + 标会话
6. 标题摘要 + 迁移
7. 测试 + 全量回归

## 风险
- transcript 隔离是最大重构面（app.py `self.transcript` 引用路由化）
- 权限排队引入"等待权限"状态点
- 旧库 `runs` 无 `title` 列——迁移幂等

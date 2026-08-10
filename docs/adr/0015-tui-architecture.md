# ADR-0015: 基于 Textual 的 TUI 渲染层

- **日期**: 2026-07-28
- **状态**: accepted

## 背景

项目原仅提供 CLI 前端（Rich 渲染器，`vague-code <task>`）。用户需要全屏交互式界面来查看流式 LLM 输出、通过交互式对话框确认工具权限、管理会话历史，并实时查看运行状态指标。

## 约束条件

- 必须与现有的 `StreamEventVisitor` 分发机制兼容——TUI 需实现同一个 `dispatch_event` 协议
- Agent 运行时在主线程上同步阻塞——TUI 需将 Agent 运行在独立后台线程中
- 权限确认（`_on_permission` 回调）需在 Agent 线程中阻塞等待 TUI 对话框确认后返回
- 必须兼容当前依赖列表中的 `rich`（同一命名空间）
- 支持 Windows 终端（VT 序列）

## 考虑方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| **Textual**（已选） | 与 Rich 同属 Textualize 生态；原生 Rich 标记；`@work(thread=True)` + `call_from_thread()` 线程安全；`ModalScreen` 原生支持对话框；`RichLog` 支持流式输出 | 增加 ~1.5MB 依赖 |
| prompt_toolkit | 轻量；成熟的输入处理 | 无富文本分割布局（无 RichLog、无面板）；需自行实现滚动/历史 |
| Urwid | 终端兼容性好 | 底层 UI 架构；需大量样板代码；与 Rich 标记不兼容 |

## 决策

选择 **Textual**。

## 架构

```
        主 asyncio 线程（Textual）                   后台线程（Agent）
  ┌─────────────────────────────┐         ┌─────────────────────┐
  │ VagueCodeApp                    │         │ Agent.run()         │
  │  ┌─────────┐ ┌──────────┐  │ call_from_thread()          │
  │  │ RichLog │ │ Sidebar  │──┼────► on_stream_event         │
  │  │ (conv)  │ │(sessions)│  │         ▲                     │
  │  ├─────────┼ ───────────┤  │         │                     │
  │  │StatusBar│ │ CmdInput │  │         │                     │
  │  └─────────┴ ───────────┘  │         │                     │
  │                            │ on_trajectory_event          │
  │  push_screen_wait() ◄──────┼────► _thread_permission      │
  │  (PermissionDialog)        │         │                     │
  └─────────────────────────────┘         └─────────────────────┘
```

### 事件流程

1. **StreamEvent（LLM → UI）**：`Agent._run_gen` → `yield StreamEvent` → worker 线程 `call_from_thread(dispatch_event)` → `TextualStreamVisitor` → `ConversationView` 更新
2. **Permission（UI → Agent）**：`_run_gen` → `_on_permission` → `asyncio.run_coroutine_threadsafe` → `push_screen_wait(PermissionDialog)` → 返回 `Decision`
3. **Tool Result（Agent → UI）**：`Agent._fire_on_tool_result` → `call_from_thread` → `ConversationView.add_tool_result()`
4. **State（Agent → UI）**：`Agent._fire_state_change` → `call_from_thread` → `StatusBar` reactive 属性更新

### 组件树

```
VagueCodeApp(App)
├── Sidebar (VerticalScroll)
│   ├── "Recent Sessions" header
│   ├── ListView (clickable sessions)
│   ├── "Memory" header
│   └── Pinned memory items
├── ConversationView (VerticalScroll)
│   └── Static content blocks (text/thinking/tool_use/tool_result)
├── StatusBar (Static + reactive)
└── CommandInput (Input)
    └── /slash command dispatch
```

## 后果

### 正面
- 完整的 TUI 界面：流式输出 / 折叠 / sidepanel / 状态 / 权限对话框 / 斜杠命令
- `vague-code tui <task>` 子命令保持 CLI 侧不变
- 复用 `dispatch_event` + `NullVisitor` + `StreamEvent` IR —— 无需改动核心层

### 负面
- 增加 4 个依赖：`textual`、`markdown-it-py`、`mdit-py-plugins`、`platformdirs`
- Worker 线程与主 asyncio 循环之间的桥接需要 9 个 `call_from_thread` 点
- TUI 启动需要等 RichConsole 初始化，增加 ~200ms 冷启动时间

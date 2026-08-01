# 细纲：11-cli-and-tui.md

**预估行数：** ~400 行
**定位：** 两个用户界面的完整实现。

---

## 开头

- **谁需要读：** 想理解两个用户界面实现细节的开发者
- **前置阅读：** 04-agent-runtime.md（理解 Agent 的编程接口）
- **读完能做什么：** 理解 CLI/TUI 如何复用 Agent 库、如何渲染流式事件、如何管理键绑定

---

## 细纲

**Part A：CLI（~160 行）**

### 1. CLI 概述（~20 行）

- thin shell 原则：CLI 只是 Agent 库的薄壳（ADR-0001）
- 入口点：`xcode` → `src/cli/__init__.py:main()`
- 职责链：参数解析 → AgentConfig 构造 → Backend 创建 → Agent 实例 → start → dispatch StreamEvent

### 2. 入口与参数（~50 行）

**`main()`（`cli/__init__.py:17-128`）：**

**参数一览表：**

| 参数 | 类型 | 默认值 | 代码位置 |
|------|------|--------|---------|
| task | positional | — | `cli/__init__.py:29` |
| workdir | positional | "." | `cli/__init__.py:30` |
| --resume | kwarg | — | `cli/__init__.py:31` |
| --model | kwarg | "deepseek-v4-flash" | `cli/__init__.py:32` |
| --max-turns | int | 20 | `cli/__init__.py:33` |
| --db-path | kwarg | "runs/runs.db" | `cli/__init__.py:34` |
| --export-jsonl | kwarg | — | `cli/__init__.py:35` |
| --stream / --no-stream | bool | --stream (true) | `cli/__init__.py:37-40` |
| --retry / --no-retry | bool | --retry (true) | `cli/__init__.py:42-45` |
| --retry-max-attempts | int | 5 | `cli/__init__.py:46-47` |
| --retry-base-s | float | 2.0 | `cli/__init__.py:48-49` |
| --retry-max-delay-s | float | 120.0 | `cli/__init__.py:50-51` |
| --timeout-s | float | 120.0 | `cli/__init__.py:52-53` |
| --provider | kwarg | "deepseek" | `cli/__init__.py:54-55` |
| --verbose | bool | false | `cli/__init__.py:56` |

**`_resolve_api_key()`（`cli/__init__.py:183-190`）：**
```python
def _resolve_api_key(provider: str) -> str | None:
    env_file = dotenv_values()          # .env 文件
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "DEEPSEEK_API_KEY"
    key = env_file.get(key_name)
    if key:
        return key
    import os
    return os.environ.get(key_name)     # 环境变量兜底
```

### 3. Rich 渲染器（~40 行）

**`RichStreamVisitor`（`cli/renderer.py`）：**

实现 `StreamEventVisitor` Protocol，每种 StreamEvent 的渲染方式：

| StreamEvent | 渲染方式 | Rich API |
|-------------|---------|----------|
| MessageStart | 显示模型名（verbose） | `print(f"Model: ...")` |
| ThinkingDelta | 以灰色/暗淡文本打印（verbose） | `console.print(text, style="dim")` |
| TextDelta | 累积流式打印 | `console.print(text, end="")` |
| ToolUseStart | 显示工具名和参数摘要 | `console.print(f"[Tool: {name}({input}):]")` |
| ToolResultBlock | 显示结果头部 | `console.print(content[:200])` |
| RetryNotice | 警告颜色 | `console.print(text, style="yellow")` |

### 4. 退出码与错误处理（~30 行）

| 场景 | 退出码 | 说明 |
|------|--------|------|
| 正常完成 | 0 | Agent end_turn / resume 完成 |
| API Key 缺失 | 1 | 提示用户设置环境变量或 .env |
| 其他 fatal error | 1 | 异常捕获 `print(e)` + `sys.exit(1)` |
| --export-jsonl 路径为目录 | 1 | `Error: --export-jsonl path is a directory` |

**`dispatch_event(ev, visitor)`**：CLI 的渲染入口（`cli/__init__.py:108-109`）

**Part B：TUI（~200 行）**

### 5. TUI 概述（~30 行）

- 两个界面共用同一个 Agent 库
- `xcode tui <task>` → `_tui_main()`（`cli/__init__.py:130-180`）→ `src/tui/__init__.py:main()` → `XClawApp`
- 基于 Textual 框架的全屏交互式终端界面
- ADR-0015 架构设计

### 6. 架构：Agent 在 @work(thread=True) 中同步运行（~50 行）

**线程桥接问题（`app.py:102-119`）：**

```
Textual 主循环（asyncio）      Agent 线程（ThreadPoolExecutor）
     │                              │
     │  @work(thread=True)           │
     │  ───────────────────────────→ │  agent.start() → _run_gen()
     │                              │
     │  call_from_thread(on_ev)      │
     │  ←─────────────────────────── │  yield StreamEvent
     │                              │
     │  asyncio.run_coroutine_thread  │
     │  safe(future, loop)          │
     │  ←─────────────────────────── │  _on_permission → push_screen_wait
```

**三层回调桥接：**

| 回调 | Agent 线程 → TUI 主循环 | 代码位置 |
|------|-------------------------|---------|
| `_on_permission` | `asyncio.run_coroutine_threadsafe(coro, loop)` → `push_screen_wait`（等待用户确认） | `app.py:142-152` |
| `on_tool_result` | `call_from_thread(on_tool_result)` → `ConversationView.add_tool_result()` | `app.py:164-173` |
| `on_state_change` | `call_from_thread(on_state_change)` → `StatusBar` 刷新 | `app.py:175-193` |

**协作式退出：** `worker.is_cancelled` → `handle.close()`（`app.py:115-117`）

### 7. 4 个布局区域（~60 行）

**Conversation View [75%]（`widgets/conversation.py`）：**
- 流式 LLM 输出渲染（与 Rich 类似但有折叠展开功能）
- `TextualStreamVisitor`（`visitor.py`）实现 `StreamEventVisitor` Protocol
- 可折叠块：
  - **thinking**：默认折叠，按 `T` 全局切换
  - **tool result**：按 `E` 展开/折叠当前聚焦的块
- `Tab` / `Shift+Tab`：在可折叠块之间导航

**Sidebar [25%]（`widgets/sidebar.py`）：**
- 历史会话列表：从 SQLite `runs` 表加载
- 点击 → `SessionDetail` screen（`screens/session_detail.py`）
- SessionDetail 操作：查看详情 → `resume` / `delete`
- 已注入记忆面板

**Status Bar（`widgets/status_bar.py`）：**
- 运行状态指示器：`●`（运行中）/ `✓`（完成）/ `✗`（失败）/ `○`（空闲）
- 轮次信息：`Turn N/M`
- Token 信息：`In: X  Out: Y`
- 压缩信息：`Reclaimed: X`
- 权限模式：`Mode: normal`
- 更新入口（`app.py:175-193` `_on_state_change()`）

**Command Input（`widgets/command_input.py`）：**
- Textual Input widget
- `/` 聚焦 → 键入 → `Enter` 提交 → `on_command_input_submitted`（`app.py:280-296`）
- 非斜杠命令 → 清空对话 → 重新 `_start_agent()`

### 8. 权限对话框（~30 行）

**代码位置：** `screens/permission.py` `PermissionDialog` + `app.py:154-162`

**交互流程：**
1. Agent 线程：`_check_tool_permission()` → `_on_permission(op, CONFIRM)`
2. TUI：`_thread_permission()` → `asyncio.run_coroutine_threadsafe` → `push_screen_wait`
3. 显示操作详情（tool_name + input + command）
4. 用户选择：
   - `Y` → ALLOW 一次（不持久化）
   - `Ctrl+Y` → ALLOW + 持久化规则到 `.agent/permission-rules.json` + `agent.add_permission_rule()`
   - `N` / 超时 → DENY

**持久化规则格式：**
```json
[
  {"pattern": "write_file *", "action": "allow"}
]
```

### 9. 会话侧边栏（~20 行）

**SessionDetail screen（`screens/session_detail.py`）：**
- 显示 run_id、task、status、created_at
- 操作按钮：resume / delete
- resume 流程（`app.py:254-276` `_start_resume_agent()`）：
  1. `Trajectory.from_db(run_id, db_path)` 从 SQLite 加载
  2. 创建新 Agent 实例（设置权限/状态回调）
  3. 加载持久规则
  4. `agent.resume(traj)` → exhaust

### 10. 键绑定参考表（~20 行）

**来源：** `XClawApp.BINDINGS`（`app.py:36-44`）

| 键 | 操作 | 行为 |
|-------|--------|------|
| `Ctrl+C` | `action_stop_agent` | 取消当前正在运行的 worker |
| `T` | `action_toggle_thinking` | 全局切换 thinking 块展开/折叠 |
| `E` | `action_toggle_expand` | 展开/折叠当前聚焦的 tool result 块 |
| `Tab` | `action_select_next` | 导航到下一个可折叠块 |
| `Shift+Tab` | `action_select_prev` | 导航到上一个可折叠块 |
| `/` | `action_focus_input` | 聚焦 Command Input |
| `Escape` | `action_cancel` | 关闭弹窗/返回/聚焦输入 |
| `F1` | `action_show_help` | 弹出帮助屏幕 |

### 11. 斜杠命令参考表（~20 行）

**来源：** `_handle_slash()`（`app.py:298-335`）

| 命令 | 操作 | 说明 |
|--------|--------|------|
| `/mode safe` | 设置 safe 模式 | 只读，修改和命令全禁止 |
| `/mode normal` | 设置 normal 模式 | 写需确认（默认） |
| `/mode autoedit` | 设置 autoedit 模式 | 写自动放行，命令仍需确认 |
| `/mode auto` | 设置 auto 模式 | 最大自动（危险命令仍需确认） |
| `/clear` | 清空对话 | 清除 Conversation View |
| `/save [path]` | 导出轨迹 | 默认 `runs/{run_id}.jsonl` |
| `/help` | 帮助 | 弹出 HelpScreen |
| `/quit` | 退出 | 退出 TUI |

---

## 结尾

**下一篇推荐：** → 12-evaluation-harness.md（自动化评测框架）
**相关 ADR：** 0001（Agent 即库）、0015（TUI 架构）

---

## 本文件说明

这是文档 `11-cli-and-tui.md` 的细纲（大纲）。实际写作时需确认 `tui/screens/permission.py` 和 `tui/widgets/` 下各 widget 的具体实现细节。键绑定和斜杠命令表需与源码同步。

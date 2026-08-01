# CLI and TUI

**谁需要读：** 想理解两个用户界面实现细节的开发者
**前置阅读：** 04-agent-runtime.md（理解 Agent 的编程接口）
**读完能做什么：** 理解 CLI/TUI 如何复用 Agent 库、如何渲染流式事件、如何管理键绑定

---

## Part A：CLI

### 1. CLI 概述

CLI 遵循 thin shell 原则（ADR-0001）：它只是 Agent 库的一层薄壳，不做任何业务逻辑。

职责链：`参数解析 → AgentConfig 构造 → Backend 创建 → Agent 实例 → start → dispatch StreamEvent`

入口点：`xcode` → `src/cli/__init__.py:main()`

### 2. 入口与参数

**main()**（`cli/__init__.py:17-128`）：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| task | positional | — | 任务描述 |
| workdir | positional | "." | 工作目录 |
| --resume | kwarg | — | 从 run_id 恢复 |
| --model | kwarg | "deepseek-v4-flash" | 模型名称 |
| --max-turns | int | 20 | 最大轮次 |
| --db-path | kwarg | "runs/runs.db" | 轨迹数据库路径 |
| --export-jsonl | kwarg | — | 导出轨迹路径 |
| --stream / --no-stream | bool | true | 流式/非流式 |
| --retry / --no-retry | bool | true | 启用重试 |
| --retry-max-attempts | int | 5 | 最大重试次数 |
| --retry-base-s | float | 2.0 | 退避基数（秒） |
| --retry-max-delay-s | float | 120.0 | 最大退避间隔 |
| --timeout-s | float | 120.0 | 请求超时 |
| --provider | kwarg | "deepseek" | LLM 提供商 |
| --verbose | bool | false | 详细输出 |

**API Key 解析**（`cli/__init__.py:183-190`）：

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

先读 `.env` 文件，再读环境变量。这个优先级保证 `.env` 可以覆盖全局环境变量，方便项目级配置。

### 3. Rich 渲染器

**RichStreamVisitor**（`cli/renderer.py`）实现 `StreamEventVisitor` Protocol，每种事件的渲染方式：

| StreamEvent | 渲染方式 | Rich API |
|-------------|---------|----------|
| MessageStart | 显示模型名（verbose 模式） | `print(f"Model: ...")` |
| ThinkingDelta | 灰色/暗淡文本（verbose 模式） | `console.print(text, style="dim")` |
| TextDelta | 累积流式打印 | `console.print(text, end="")` |
| ToolUseStart | 显示工具名和参数摘要 | `console.print(f"[Tool: {name}({input}):]")` |
| ToolResultBlock | 显示结果头部 | `console.print(content[:200])` |
| RetryNotice | 警告颜色 | `console.print(text, style="yellow")` |

CLI 的渲染在非 verbose 模式下只显示 TextDelta 和 ToolUseStart/ToolResult 摘要，Thinking 和纯技术细节默认隐藏。

### 4. 退出码与错误处理

| 场景 | 退出码 | 说明 |
|------|--------|------|
| 正常完成 | 0 | Agent end_turn / resume 完成 |
| API Key 缺失 | 1 | 提示用户设置环境变量或 .env |
| 其他 fatal error | 1 | `print(e)` + `sys.exit(1)` |
| --export-jsonl 路径为目录 | 1 | 提示错误 |

---

## Part B：TUI

### 5. TUI 概述

TUI 与 CLI 使用同一个 Agent 库（ADR-0001）。启动方式：

```
xcode tui <task>
```

职责链：`_tui_main()`（`cli/__init__.py:130-180`）→ `src/tui/__init__.py:main()` → `XClawApp`

基于 Textual 框架的全屏交互式终端界面。

### 6. 架构：Agent 在线程中同步运行

Agent Runtime 是同步的（零 asyncio 约束）。但 Textual 基于 asyncio。如何桥接？

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

Agent 在 `@work(thread=True)` 的线程中同步运行，通过回调桥接与 TUI 主循环通信。三层回调：

| 回调 | Agent 线程 → TUI 主循环 | 代码位置 |
|------|-------------------------|---------|
| `_on_permission` | `asyncio.run_coroutine_threadsafe(coro, loop)` → `push_screen_wait` | `app.py:142-152` |
| `on_tool_result` | `call_from_thread(on_tool_result)` → `ConversationView.add_tool_result()` | `app.py:164-173` |
| `on_state_change` | `call_from_thread(on_state_change)` → `StatusBar` 刷新 | `app.py:175-193` |

协作式退出：`worker.is_cancelled` → `handle.close()`（`app.py:115-117`）——用户按 Ctrl+C 取消时，Agent 线程感知取消信号并停止生成器。

### 7. 4 个布局区域

TUI 主界面分为四个区域：

**Conversation View [75%]**（`widgets/conversation.py`）：
- 流式 LLM 输出渲染，比 Rich 多了折叠展开功能
- `TextualStreamVisitor`（`visitor.py`）实现 `StreamEventVisitor` Protocol
- 可折叠块：
  - **thinking**：默认折叠，按 `T` 全局切换
  - **tool result**：按 `E` 展开/折叠当前聚焦的块
- `Tab` / `Shift+Tab`：在可折叠块之间导航

**Sidebar [25%]**（`widgets/sidebar.py`）：
- 历史会话列表：从 SQLite `runs` 表加载
- 点击 → `SessionDetail` screen（`screens/session_detail.py`），可查看详情、resume、delete
- 已注入记忆面板，显示最近的 episodic 蒸馏记忆

**Status Bar**（`widgets/status_bar.py`）：
- 运行状态指示器：`●`（运行中）/ `✓`（完成）/ `✗`（失败）/ `○`（空闲）
- 轮次信息：`Turn N/M`
- Token 信息：`In: X  Out: Y`
- 压缩信息：`Reclaimed: X`
- 权限模式：`Mode: normal`
- 更新入口：`_on_state_change()`（`app.py:175-193`）

**Command Input**（`widgets/command_input.py`）：
- Textual Input widget
- `/` 聚焦 → 键入 → Enter 提交 → `on_command_input_submitted`（`app.py:280-296`）
- 非斜杠命令 → 清空对话 → 重新 `_start_agent()`

### 8. 权限对话框

**代码位置：** `screens/permission.py` `PermissionDialog` + `app.py:154-162`

交互流程：
1. Agent 线程：`_check_tool_permission()` → `_on_permission(op, CONFIRM)`
2. TUI：`_thread_permission()` → `asyncio.run_coroutine_threadsafe` → `push_screen_wait`
3. 显示操作详情（tool_name + input + command）
4. 用户选择：
   - **Y** → ALLOW 一次（不持久化）
   - **Ctrl+Y** → ALLOW + 持久化规则到 `.agent/permission-rules.json` + `agent.add_permission_rule()`
   - **N** / 超时 → DENY

持久化规则格式：
```json
[
  {"pattern": "write_file *", "action": "allow"}
]
```

### 9. 会话侧边栏

**SessionDetail screen**（`screens/session_detail.py`）：
- 显示 run_id、task、status、created_at
- 操作按钮：resume / delete

Resume 流程（`app.py:254-276`）：
1. `Trajectory.from_db(run_id, db_path)` 从 SQLite 加载
2. 创建新 Agent 实例（设置权限/状态回调）
3. 加载持久规则
4. `agent.resume(traj)` → exhaust

### 10. 键绑定参考表

来源：`XClawApp.BINDINGS`（`app.py:36-44`）

| 键 | 操作 | 行为 |
|-------|--------|------|
| Ctrl+C | `action_stop_agent` | 取消当前正在运行的 worker |
| T | `action_toggle_thinking` | 全局切换 thinking 块展开/折叠 |
| E | `action_toggle_expand` | 展开/折叠当前聚焦的 tool result 块 |
| Tab | `action_select_next` | 导航到下一个可折叠块 |
| Shift+Tab | `action_select_prev` | 导航到上一个可折叠块 |
| / | `action_focus_input` | 聚焦 Command Input |
| Escape | `action_cancel` | 关闭弹窗/返回/聚焦输入 |
| F1 | `action_show_help` | 弹出帮助屏幕 |

### 11. 斜杠命令参考表

来源：`_handle_slash()`（`app.py:298-335`）

| 命令 | 操作 | 说明 |
|--------|--------|------|
| `/mode safe` | 设置 safe 模式 | 只读，修改和命令全禁止 |
| `/mode normal` | 设置 normal 模式 | 写需确认（默认） |
| `/mode autoedit` | 设置 autoedit 模式 | 写自动放行，命令仍需确认 |
| `/mode auto` | 设置 auto 模式 | 最大自动 |
| `/clear` | 清空对话 | 清除 Conversation View |
| `/save [path]` | 导出轨迹 | 默认 `runs/{run_id}.jsonl` |
| `/help` | 帮助 | 弹出 HelpScreen |
| `/quit` | 退出 | 退出 TUI |

---

## 下一篇

→ **12-evaluation-harness.md**：自动化评测框架——消融实验、FakeBackend、报告生成。

**相关 ADR：** 0001（Agent 即库）、0015（TUI 架构）

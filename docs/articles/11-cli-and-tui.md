# CLI and TUI

**谁需要读：** 想理解两个用户界面实现细节的开发者
**前置阅读：** 04-agent-runtime.md（理解 Agent 的编程接口）
**读完能做什么：** 理解 CLI/TUI 如何复用 Agent 库、如何渲染流式事件、如何管理键绑定

---

## Part A：CLI

### 1. CLI 概述

CLI 遵循 thin shell 原则（ADR-0001）：它只是 Agent 库的一层薄壳，不做任何业务逻辑。

职责链：`参数解析 → AgentConfig 构造 → Backend 创建 → Agent 实例 → start → dispatch StreamEvent`

入口点：`vague-code` → `vague_code/cli/__init__.py:main()`

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
vague-code tui [task]
```

职责链：`_tui_main()`（`cli/__init__.py:136-186`）→ `vague_code/tui/__init__.py:main()` → `VagueCodeApp`

基于 Textual 框架的全屏交互式终端界面。v2 采用分层架构（ADR-0019）：UI 是薄壳，所有能力通过 runner / command handler 注入，事件全部写入 `TuiTranscript` 单一事实源，再驱动渲染。代码不再使用 `StreamEventVisitor`（`visitor.py` 已删除）——Agent 线程的事件经 `VagueCodeAgentRunner` 回调直达 transcript。

### 6. 架构：Agent 在线程中同步运行

Agent Runtime 是同步的（零 asyncio 约束）。但 Textual 基于 asyncio。如何桥接？

```
Textual 主循环（asyncio）      Agent 线程（run_worker(thread=True)）
     │                              │
     │  run_worker(thread=True)      │
     │  ───────────────────────────→ │  VagueCodeAgentRunner.run_task()
     │                               │  agent.start() → _run_gen()
     │  call_from_thread(on_ev)      │
     │  ←─────────────────────────── │  yield StreamEvent
     │                               │
     │  run_coroutine_threadsafe     │
     │  ←─────────────────────────── │  _on_permission → push_screen_wait
```

关键组件：

| 组件 | 文件 | 职责 |
|------|------|------|
| `VagueCodeApp` | `app.py` | 薄壳：compose / bindings / 事件分发 / 回合管理 |
| `VagueCodeAgentRunner` | `runner.py` | 同步 Agent ↔ 异步 UI 桥：事件回调、权限桥、取消、guidance、permission rules |
| `VagueCodeViewMixin` | `mixin.py` | 流式 Markdown 三层缓冲（0.2s flush + update guard）、活动动画、回合 metrics |
| `TuiTranscript` | `state.py` | 展示态单一事实源（entries 带 widget 引用） |
| views/ | `views/*.py` | 纯函数渲染（topbar / activity / welcome / transcript / review），可独立单测 |
| commands/ | `commands/*.py` | `CompositeCommandHandler` + `CommandResult(handled, output, action)` |
| `VagueCodeMarkdown` | `widgets/common.py` | 流式期间禁选、finalize 后放开的选择门控 |

**三层回调**（Agent 线程 → TUI 主循环，均经 `call_from_thread`）：

| 回调 | 用途 | 代码位置 |
|------|------|---------|
| `on_stream_event` | StreamEvent 分发（text/thinking/tool/retry/end） | `app.py:_on_stream_event` |
| `on_tool_result(tool_id, name, content, is_error)` | 工具结果（带 tool id 关联工具条目） | `app.py:_on_tool_result` |
| `on_permission(op, decision)` | 阻塞式权限确认（`run_coroutine_threadsafe` + `push_screen_wait`） | `app.py:_thread_permission` |
| `on_state_change(kind, payload)` | turn_start / llm_response / compression | `app.py:_on_state_change` |

**turn token 过期过滤**：每回合生成单调递增 token，事件闭包携带 token，`token != _chat_turn_token` 的事件直接丢弃——中断/恢复后旧事件不会污染新回合。

**协作式退出**：`worker.is_cancelled` → `handle.close()`——Esc 两次（1 秒窗口）或 Ctrl+C 中断时，Agent 线程感知取消信号并停止生成器。

### 7. 布局与渲染

TUI 主界面分为四个区域：

**Topbar**（`views/topbar.py`）：
- 五段式：`vague-code · 状态 · provider/model · 权限模式 · cwd`
- 按 Rich 渲染宽度截断（不是字符数），窄屏逐段丢弃右侧段

**Conversation View**（`widgets/conversation.py`）：
- transcript 驱动：每条 `TuiTranscriptEntry` 渲染为 widget 并回写 `entry.widget`，支持原地更新
- 流式 Markdown：0.2s 定时 flush + `_stream_markdown_update` future guard 防乱序覆盖；流式期间不可选中，finalize 后放开
- thinking 超 200 词自动折叠为摘要（`T` 展开/收起）
- 工具条目：`正在调用工具：name args预览` → 完成后原地更新为 `工具完成/失败：name 摘要`
- 自动滚动仅当用户停在底部时（pinned）

**Activity Line**（`widgets/status.py`）：
- 状态动画：`thinking [.  ]…` / `running [=   ] · bash` / `streaming [>   ] · response`
- 右侧回合指标：`12.3s · 2 tools`；并行工具显示 `N tools running`

**Composer**（`widgets/common.py` `ComposerTextArea`）：
- `Enter` 发送，`Shift+Enter` 换行
- 运行中发送 → 进入 guidance 队列（`_add_guidance`），agent 回合开头经 `guidance_provider` 注入为 user 消息（loop.py `_drain_guidance`）；回合结束若队列仍有残留则自动开新回合
- `↑`/`↓` 输入历史

### 8. 权限对话框（含写入审查）

**代码位置：** `screens/permission.py` `PermissionDialog` + `app.py:_thread_permission`

交互流程：
1. Agent 线程：`_check_tool_permission()` → 对 `write_file`/`patch` 先计算 prewrite diff（`vague_code/agent/prewrite.py`，纯函数）挂到 `op.review`
2. TUI：`_thread_permission()` → `asyncio.run_coroutine_threadsafe` → `push_screen_wait`
3. 弹窗显示操作详情 + **写入前 diff 预览**（红删绿增，`views/review.py` 渲染）+ 可选的拒绝理由输入框
4. 用户选择：
   - **Y** → ALLOW 一次（不持久化）
   - **Ctrl+Y** → ALLOW + 持久化规则到 `.agent/permission-rules.json` + `agent.add_permission_rule()`
   - **N** / 超时（120s）→ DENY；若填了拒绝理由（`op.feedback`），理由并入返回模型的错误消息（反馈闭环）

持久化规则格式：
```json
[
  {"pattern": "write_file *", "action": "allow"}
]
```

### 9. 会话与 resume

侧边栏已移除（ADR-0019）。会话管理全部走命令：

- **`/resume`** → picker 列出 `runs` 表最近会话 → 选中后重新路由 `/resume <run_id>`
- **Resume 流程**（`app.py:_start_resume`）：
  1. `Trajectory.from_db(run_id, db_path)` 加载
  2. `_replay_trajectory()` 从事件流重建 transcript（user 任务 / assistant Markdown / 工具调用与结果生命周期）
  3. `VagueCodeAgentRunner.resume(traj)` 静默恢复运行（不 yield 事件，与 replay 互补）
- **`/model`** → picker 选择预设模型（deepseek-v4-flash / v4-pro / chat / reasoner），选中后重路由 `/model <name>` 更新 config

### 10. 键绑定参考表

来源：`VagueCodeApp`（`app.py` BINDINGS + `on_key`）

| 键 | 操作 | 行为 |
|-------|--------|------|
| Enter | 发送 | 提交 composer |
| Shift+Enter | 换行 | composer 插入换行（部分终端不支持时可用 `Ctrl+J`） |
| ↑ / ↓ | 输入历史 | 焦点在输入框时回忆历史 |
| Esc | 聚焦输入框 | 空闲时聚焦；运行中**按两次**（1 秒窗口）中断回合 |
| Ctrl+C | 复制/中断 | 有选中先复制 → 运行中中断（**不退出**） |
| T | `action_toggle_thinking` | 折叠/展开 thinking 块 |
| F1 | `action_show_help` | 输出帮助（`/help`） |

### 11. 斜杠命令参考表

来源：`commands/`（`CompositeCommandHandler`，命令即唯一事实源，picker 选中后重路由命令）

| 命令 | 操作 | 说明 |
|--------|--------|------|
| `/help` | 帮助 | 输出命令与快捷键列表（同 `F1`） |
| `/resume` | resume picker | 选择历史会话继续（先重放历史） |
| `/new` | 新会话 | 清空输出并显示欢迎页 |
| `/clear` | 清空对话 | 清除 Conversation View |
| `/save [path]` | 导出轨迹 | 默认 `runs/{run_id}.jsonl` |
| `/model` | 模型 picker | 无参数弹出模型选择 |
| `/model <name>` | 直接切换 | 更新 `config.model` 并刷新 topbar |
| `/mode <m>` | 权限模式 | `safe\|normal\|autoedit\|auto` |
| `/permissions` | 规则列表 | 列出 `.agent/permission-rules.json` |
| `exit` | 退出 | 直接输入 exit（唯一退出方式） |

---

## 下一篇

→ **12-evaluation-harness.md**：自动化评测框架——消融实验、FakeBackend、报告生成。

**相关 ADR：** 0001（Agent 即库）、0015（TUI 架构）、0019（TUI v2 分层重写）

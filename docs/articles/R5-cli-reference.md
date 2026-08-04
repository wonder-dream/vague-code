# R5：CLI 参考

**谁需要读：** 命令行用户
**前置阅读：** T1（第一个任务）
**读完能做什么：** 掌握所有 CLI flag、子命令、环境变量、退出码、TUI 键绑定和斜杠命令

---

## 1. CLI 入口

**代码位置：** `cli/__init__.py:17-128` `main()`

**命令格式：** `xcode [flags] [task] [workdir]`

**子命令路由：** `argv[0] == "tui"` → `_tui_main()`（`cli/__init__.py:20-22`）

---

## 2. CLI Flag 表

| Flag | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| task | positional | — | 任务描述 |
| workdir | positional | `"."` | 工作目录 |
| `--resume RUN_ID` | kwarg | — | 恢复指定 run |
| `--model` | kwarg | `"deepseek-v4-flash"` | 模型名 |
| `--max-turns` | int | 20 | 最大轮次 |
| `--db-path` | kwarg | `"runs/runs.db"` | SQLite 数据库路径 |
| `--export-jsonl` | kwarg | — | 导出轨迹到 JSONL |
| `--stream` / `--no-stream` | bool | `--stream` | 流式输出 |
| `--retry` / `--no-retry` | bool | `--retry` | 启用重试 |
| `--retry-max-attempts` | int | 5 | 最大重试次数 |
| `--retry-base-s` | float | 2.0 | 退避基数（秒） |
| `--retry-max-delay-s` | float | 120.0 | 最大退避间隔（秒） |
| `--timeout-s` | float | 120.0 | 单轮超时（秒） |
| `--provider` | kwarg | `"deepseek"` | 提供商 |
| `--verbose` | bool | false | 详细输出 |

**互斥组：** `--stream` / `--no-stream`、`--retry` / `--no-retry`

---

## 3. TUI 子命令

**代码位置：** `cli/__init__.py:136-186` `_tui_main()`

| Flag | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| tui `<task>` | positional + 子命令 | — | 启动 TUI 模式 |
| tui `[workdir]` | positional | `"."` | 工作目录 |
| `--model` | kwarg | `"deepseek-v4-flash"` | 模型名 |
| `--max-turns` | int | 20 | 最大轮次 |
| `--db-path` | kwarg | `"runs/runs.db"` | SQLite 数据库路径 |
| `--provider` | kwarg | `"deepseek"` | 提供商 |
| `--timeout-s` | float | 120.0 | 单轮超时 |
| `--retry-max-attempts` | int | 5 | 最大重试次数 |
| `--retry-base-s` | float | 2.0 | 退避基数 |
| `--retry-max-delay-s` | float | 120.0 | 最大退避间隔 |

---

## 4. 环境变量

| 变量 | 说明 | 位置 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | `.env` 或系统环境变量 |
| `ANTHROPIC_API_KEY` | Anthropic API Key | `.env` 或系统环境变量 |

**优先级：** `.env` 文件 → 系统环境变量（`cli/__init__.py:183-190`）

---

## 5. 退出码

| 退出码 | 说明 | 触发 |
|--------|------|------|
| 0 | 正常 | Agent 正常完成 / resume 完成 |
| 1 | 错误 | API Key 缺失 / export 路径为目录 / fatal error |

---

## 6. TUI 键绑定

**来源：** `app.py`（`BINDINGS` + `on_key`）

| 键 | 操作 | 行为 |
|-------|--------|------|
| Enter | 发送 | 提交 composer |
| Shift+Enter | 换行 | composer 插入换行 |
| ↑ / ↓ | 输入历史 | 焦点在输入框时回忆历史 |
| Esc | 聚焦输入框 | 空闲时聚焦；运行中按两次（1 秒窗口）中断回合 |
| Ctrl+C | 复制/中断/退出 | 有选中先复制 → 运行中中断 → 否则退出 |
| T | `toggle_thinking` | 折叠/展开 thinking 块 |
| F1 | `show_help` | 输出帮助（同 `/help`） |

---

## 7. TUI 斜杠命令

**来源：** `commands/`（`CompositeCommandHandler`）

| 命令 | 操作 | 说明 |
|--------|--------|------|
| `/help` | 帮助 | 命令与快捷键列表 |
| `/resume` | resume picker | 选择历史会话继续（先重放历史再恢复） |
| `/new` | 新会话 | 清空输出并显示欢迎页 |
| `/clear` | 清空对话 | 清除 Conversation View |
| `/save [path]` | 导出轨迹 | 默认 `runs/{run_id}.jsonl` |
| `/model` | 模型 picker | 弹出预设模型选择 |
| `/model <name>` | 直接切换模型 | 更新 config 并刷新 topbar |
| `/mode safe/normal/autoedit/auto` | 设置权限模式 | 后续回合生效，无需重启 |
| `/permissions` | 列出权限规则 | `.agent/permission-rules.json` 内容 |
| `/quit` | 退出 | 退出 TUI |

---

## 8. 使用示例

| 场景 | 命令 |
|------|------|
| 修 bug | `xcode "Fix the division by zero" ./project --max-turns 30` |
| 只读模式 | `xcode --permission-mode safe "Review auth" ./project` |
| 恢复中断 | `xcode --resume abc123456789` |
| 导出分析 | `xcode --export-jsonl traj.jsonl --no-stream "task"` |
| TUI 模式 | `xcode tui "Refactor data layer" ./project` |
| Anthropic | `xcode --provider anthropic --model claude-sonnet-4-5 "task"` |
| 评测验证 | `python -m eval.cli --tasks eval/tasks_test.json --fake` |
| 评测全量 | `python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 3` |

---

## 下一篇

→ **troubleshooting.md**——常见问题与解决方案。

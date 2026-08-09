# 0025: 会话内连续对话（CLI REPL + TUI，跨进程续会话）

- **日期**: 2026-08-09
- **状态**: approved（用户确认：CLI + TUI 都要、跨进程续会话）

## 背景

当前 `Agent.start()` 每次全新 run + 全新消息，无会话概念；`resume()` 是断点续跑（已完成 run 拒续）。TUI/CLI 均无法像 opencode/codex/cc 一样在会话内连续对话。

## 核心模型

**一个会话 = 一个 run**（run_id 即会话 id）。多轮对话共用同一轨迹、事件连续落盘；`run_start.payload["mode"] = "chat"` 标记会话（普通任务不设）。

## agent/loop.py

- `_init_run(task, workdir, mode=None)`：从 start() 抽取初始化（system prompt/repo map/memory/tools），run_start payload 带 mode
- `Agent.chat(text, workdir) -> RunHandle`：首轮走 `_init_run(mode="chat")` 并缓存会话状态；后续轮 `messages.append(user)`，turn 累计
- `Agent.chat_resume(run_id) -> RunHandle`：`Trajectory.from_db` → `to_messages()` 重建历史 → turn = last_llm.turn+1 → 继续对话（不重放工具）
- `Agent.chat_end()`：无 run_end 时 emit `run_end(reason="chat_end")` + persist；清空会话状态
- `_run_gen(*, chat_mode)`：end_turn 时暂停（保存 `_chat_turn`、不 emit run_end）；压缩后 messages 回写 `_chat_messages`；finally persist 保留

## CLI：`xcode chat [--resume <run_id>]`

Rich 流式渲染 REPL；`exit`/Ctrl+C/EOF → chat_end 退出；`/new` 新会话；`/resume <id>` 切换会话。

## TUI

- app 持有复用 `XClawAgentRunner`（懒创建），每轮调 `run_chat(text)`（首轮自动初始化）
- `/new` → `runner.end_chat()` + 清空
- `/resume` picker 分组：对话会话（mode=chat）走 `chat_resume`，任务走原 resume（自动结束当前会话）
- `_on_run_complete`：无 run_end 时活动行显示 `done · turn N`
- `session_lib.list_recent_chats`（json_extract 查 mode，不加表结构）

## 测试

- agent：多轮对话、上下文延续、压缩回写、turn 累计、chat_resume、chat_end、chat_end 后新会话
- CLI：REPL 冒烟（stdin 模拟）
- TUI：pilot 连续两轮、/resume 分组恢复

## 不变项

resume 断点续跑语义、guidance、压缩/监督/权限、DB 表结构、轨迹格式。

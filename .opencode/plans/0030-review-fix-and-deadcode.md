# 0030: 审查修复 + 死代码清理（综合三轮审查结果，逐步执行）

- **日期**: 2026-08-10
- **状态**: **done**（12 步全部执行完毕：全量 775 passed、ruff/mypy 零错误、vulture A 类清零）

## 执行记录（每步独立验证）

| Step | 内容 | 结果 |
|---|---|---|
| 1 | B1 user_message 事件 + to_messages 消费 + 实证测试 | test_chat_session 10→13 passed |
| 2 | B3 `_complete_pending_tools`（chat 续轮 + chat_resume） | 13 passed，resume 回归 95 passed |
| 3 | B5 max_tokens 同步部分回复 | 13 passed |
| 4 | B2 `resume_run_id` + `_switch_session` 接线（改写旧测试） | TUI 25 passed |
| 5 | B4 worker 竞态防护 + B6 键 remap（3 新测试） | TUI 56 passed |
| 6 | S1 CLI `--mode` + 规则加载（2 新测试） | test_cli 32 passed |
| 7 | M5 危险命令补盲（12 新测试） | test_permission 45 passed |
| 8 | 枚举 + 配置字段（4 处） | 142 passed |
| 9 | 属性 + 死参数（6 处，含测试 12 处调用点） | 113 passed + TUI 56 passed |
| 10 | 方法 + eval 层（8 处 + 连带测试） | 108 passed |
| 11 | `LayerReport.unit` + `set_current` | 61 passed + vulture 复查 |
| 12 | 全量回归 + 文档 | **775 passed** / ruff / mypy / CHANGELOG / 死代码.md / 本计划 |

## 背景

基于三轮审查结论（`审查.md` / `死代码.md` / 本会话复扫），修复 6 个新发现的
chat 会话链 Bug、2 个遗留问题（S1 CLI 不可写、M5 危险命令盲区），并清理
死代码.md A 类清单 + 新发现的 `LayerReport.unit`。

执行纪律：**每步只做一项**，每步独立验证（目标测试 + 静态检查），全部完成后再全量回归。

## Phase 1 — chat 会话链 Bug 修复

### Step 1: B1 — chat 多轮用户消息落轨迹（数据一致性）
- **问题**：`Agent.chat()` 后续轮只 append 内存消息（loop.py:252），不 emit 事件；
  `to_messages()`（trajectory.py:206）重建时丢失第 2+ 轮用户消息，且产生连续两条
  assistant 消息（角色交替违例）。已实证复现。
- **改法**：`trajectory.py` 新增 `EventType.user_message`；`chat()` 在进入
  `_run_gen` 前 emit（turn=_chat_turn，payload={"text": ...}）；`to_messages()`
  消费该事件 append `Message(role="user")`。
- **验证**：新增测试——多轮 chat（含工具轮）→ chat_end → chat_resume，断言
  恢复消息含全部用户轮次、无连续 assistant；跑 test_chat_session.py。

### Step 2: B3 — 中断/崩溃后的悬挂 tool_use 补执行
- **问题**：chat 工具执行中被中断（Esc×2/崩溃）→ `_chat_messages`/DB 残留
  assistant tool_calls 无结果 → 续聊 encode 出无结果 tool_calls → API 400。
  任务模式 `resume()` 有 `_execute_pending_tools`（loop.py:1109）兜底，chat 无。
- **改法**：chat 续轮与 `chat_resume()` 在进入 `_run_gen` 前，若消息尾是
  assistant 且含未完成 tool_use → 调用 `_execute_pending_tools` 补执行；
  补执行结果消息与新用户文本合并（避免连续 user 消息）。
- **验证**：新增测试——chat 中断于工具执行后续聊，断言工具结果存在且顺序正确。

### Step 3: B5 — max_tokens 终止时同步部分回复
- **问题**：loop.py:644-646 分支（max_tokens/content_filter/unknown）直接
  run_end return，`resp.message` 未 append 进 `_chat_messages`（end_turn 分支有）。
- **改法**：该分支 chat_mode 下先 append + 更新 `_chat_turn` 再 return（对齐
  end_turn 分支）。
- **验证**：新增测试——chat 下返回 max_tokens，下一轮 seen_messages 含截断回复。

### Step 4: B2 — 侧边栏历史会话续聊接续原会话
- **问题**：`_switch_session`（app.py:173）对 DB 会话建 state 不带 agent；
  `_submit_chat` 新建 Agent → `chat()` 走 `_init_run` 开全新 run，旧上下文丢失。
- **改法**：`SessionState` 加 `resume_run_id: str | None = None`；`_switch_session`
  DB 分支创建 agent 并按 `_resume_chat_session` 方式接线，置 `resume_run_id`；
  `_run_agent_worker` 中 `state.resume_run_id and not agent.in_chat` →
  走 `chat_resume`（成功后清空）。
- **验证**：新增 TUI 测试（test_sessions.py）——加载 DB 会话后提交消息，断言
  agent 走 chat_resume 且 run_id 不变。

### Step 5: B4 + B6 — worker 键泄漏与中断后并发竞态
- **问题**：`_session_workers` 以占位 run_id 为键，rename 后 pop 不掉旧键（泄漏，
  B6）；中断后旧 worker 若仍在 bash 执行中，`_submit_chat` 可能启动第二个 worker
  与旧生成器并发改 `_chat_messages`（竞态，B4）。
- **改法**：`_on_run_complete` rename 处同步 remap `_session_workers` 旧键→新键
  （B6）；`_submit_chat` 的 busy 判断增加"该会话存在 RUNNING 状态 worker 则并入
  队列"（B4，复用现有 guidance 队列机制）。
- **验证**：新增 TUI 测试——首轮完成后无占位键残留；中断后旧 worker 未退出前
  提交消息走队列而非新 worker。

## Phase 2 — 遗留问题修复

### Step 6: S1 — CLI 增加权限模式与规则加载
- **问题**：CLI 单次模式与 `vague-code chat` 不设 `_on_permission`、不加载
  `.agent/permission-rules.json`、无 `--mode` → normal 默认下写操作全 DENY。
- **改法**：`vague_code/cli/__init__.py` 三个入口加 `--mode {safe,normal,autoedit,auto}`
  （默认 normal）；启动时从 workdir 加载 `.agent/permission-rules.json` 注入 agent。
- **验证**：扩展 test_cli.py——`--mode auto` 生效、规则文件加载生效。

### Step 7: M5 — 危险命令模式补盲
- **问题**：`_DANGEROUS_COMMANDS` 缺 `git reset --hard`/`git clean`/`pip install`/
  `taskkill` 等（permission.py:65）。注意 bash 工具自带 `chcp 65001 >nul &` 前缀，
  **不做通用 `>` 重定向模式**（会全命令误判）。
- **改法**：补充 `git reset`/`git clean`/`git checkout --`/`git restore`/
  `pip install`/`npm install`/`taskkill` 模式。
- **验证**：扩展 test_permission.py 新模式的 classify_bash 断言。

## Phase 3 — 死代码清理（A 类 + 新增项；B/C 类不动）

> B 类（in_chat/terminal_reason/runner 三方法/active_tool）有测试契约钉住，
> 本轮不删；C 类（防御性保留）不动。以下每组删完跑对应测试。

### Step 8: 枚举与配置字段（零风险）
- `trajectory.py:34/37` 删 `retry_divergence`/`mode_change` 枚举成员；
- `config.py:63` 删 `MemoryConfig.search_top_k`；
- `config.py:72` 删 `RepoMapConfig.languages` + `repomap.py:40` `RepoIndex.languages`。
- **验证**：test_config.py / test_memory.py / test_repomap.py + vulture 复查。

### Step 9: 实例属性与死参数（零风险）
- `loop.py:63` 删 `_StreamAggregator._result`；
- `memory.py:10` 删 `_db_path`；
- `app.py:123` 删 `_pending_guidance`；
- `conversation.py:23` 删 `_pinned`；
- `memory.py:39` 删 `ingest(confidence=...)` 参数；
- `app.py:495` 删 `_begin_new_session(first_text)` 参数（同步 2 处调用点）。
- **验证**：test_memory / test_agent_loop / tui 测试 + vulture 复查。

### Step 10: 方法与 eval 层（连带测试）
- `memory.py:88` 删 `recent()` → 删 test_memory.py:43,53,60,94 四段断言；
- `widgets/sidebar.py:115` 删 `set_current()`；
- `metrics.py:186` 删 `load_gold()` → 连带删 `eval/judge.py:11` noqa import
  （C 类条目，删除 load_gold 后必须连带）；
- `judge.py:38` 删 `JudgeResult.raw_output` → 删 test_eval_judge.py:96 断言；
- `env.py:135` 删 `EnvSpec.repo_key` → 改 test_eval_verify.py:59 构造；
- `select_tasks.py:11` 删 `output_dir` 参数（检查 `__main__` 调用点）；
- `harness.py` `_FakeBackend` 删 `call_count` 自增 + `tools` 形参（确认无测试引用）。
- **验证**：test_memory / test_eval_* / test_sessions 等 + vulture 复查。

### Step 11: 新增死字段
- `context_compress.py:24` 删 `LayerReport.unit` 字段（确认无测试引用）。
- **验证**：test_compress_chain / test_microcompact 等 + vulture 复查。

## Phase 4 — 收尾

### Step 12: 全量回归 + 文档同步
- `pytest -q --ignore=tests/_target_bug` 全量；`ruff check src eval`；mypy；
- vulture 复扫确认 A 类清零；
- 更新 `死代码.md`（A 类标记已清理、B 类状态）、`CHANGELOG.md`、本计划状态。

## 验证标准（总）

1. 全量测试 0 回归（759+新增）
2. ruff/mypy 零错误
3. vulture 仅剩 D 类误报与 B/C 类有意保留
4. 每个 Bug 有对应新增测试且先红后绿（B1/B3 已红——B1 已实证）

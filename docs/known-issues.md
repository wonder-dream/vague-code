# Known Issues & Unfixed Bugs

Last updated: 2026-08-05

此文档记录项目当前已知问题状态。已修复 11 项，6 项经分析非实际缺陷，3 项为代价高/分析侧处理。

---

## 本会话已修复（11 项）

| 问题 | 修复批次 | commit |
|------|---------|--------|
| DeepSeek ThinkingBlock-only 助手消息崩溃 | A1 | `9cbe7a4` |
| `to_messages` 空 task 创建空文本消息 | A2 | `9cbe7a4` |
| `flush_results` 无去重（resume 后重复 tool_result） | A3 | `9cbe7a4` |
| `_execute_pending_tools` 结束后缺少 checkpoint | A4 | `9cbe7a4` |
| 三层截断单位不一致（bytes vs chars） | B1 | `98972d6` |
| retry 令牌估计缺 `skip_thinking` | B3 | `98972d6` |
| Anthropic 合并 assistant 后 thinking 块非首位 | C2 | `00a43f9` |
| `compress_chain` 异常导致 over-budget 消息发送 | C3 | `00a43f9` |
| `_extract_path` 只追踪多路径 grep 第一个路径 | C4 | `00a43f9` |
| `to_messages` 非确定性 system prompt 重建 | R1 | `4206cdc` |
| truncate O(P×C) 性能（per-message 令牌缓存） | R2 | `4206cdc` |
| concurrency 根级 glob / 全库 grep 不参与冲突检测 | R7 | 未提交 |
| concurrency Windows 路径大小写不敏感（normcase 归一化） | R7 | 未提交 |
| concurrency 超时后 with 块等待慢任务（shutdown wait=False） | R7 | 未提交 |
| eval harness workdir 跨进程共享，并行评测互删目录 | R8 | 未提交 |

---

## 非缺陷（经分析验证不修）

| 问题 | 原因 |
|------|------|
| 消息中 `tool_use_id` 重复时静默覆盖 | 畸形输入，正常执行路径不可达 |
| `_find_pairs` 对非 tool 的 assistant→user 对做原子绑定 | Agent 循环不创建纯文本 assistant→user 序列 |
| `max_turns` 判定偏移（resume vs non-resume） | 两路径判定逻辑一致，无实际差异 |
| Anthropic ThinkingBlock 静默丢弃 | Anthropic API 始终返回非空 signature |
| `patch` UnicodeDecodeError | 异常已被 `loop.py` 正确捕获并转换为错误消息 |
| Anthropic ThinkingBlock 无告警 | 正常路径不可达 |

---

## 真正未修复

### U1 — truncate 尾部贪心时序偏移

- **文件**: `context_compress.py:truncate`
- **影响**: 尾部贪心 + 独立消息补扫可能导致新旧 pair 超出预算时被部分丢弃，时序不完美
- **修复代价**: 高——需重写 truncate 核心算法
- **替代方案**: per-message token 缓存（已修复，R2）缓解了性能瓶颈，但算法本身不变
- **状态**: 等待重写

### U2 — resume 重复 compression 事件

- **文件**: `loop.py:finally` + `Agent.resume`
- **影响**: resume 时重新运行 _run_gen，compression 事件再次触发，产生重复 event log
- **修复**: 不修代码。分析侧按 `(run_id, turn, layer)` 去重即可
- **状态**: 文档标记

### U3 — `loop.py:505` 的 `run_end(max_turns)` 正常流程不可达

- **文件**: `loop.py:505`（while 循环后兜底）+ `loop.py:420`（轮次熔断）
- **发现**: `while turn_box[0] < max_turns` 无法因 turn_box 正常涨满而退出——因为 `loop.py:420` 的熔断检查（`turn + 1 >= max_turns`）位于 `stop_reason == tool_use` 分支内、任何工具执行之前，LLM 在最后一轮请求工具时直接 `run_end(pending)` 并 return。turn_box 永远停留在 `max_turns - 1`，`loop.py:505` 的兜底 `run_end(max_turns)` 在正常流程中**不可达**，是防御性死代码。
- **性质**: 非 bug，行为符合设计意图（max_turns 是"硬墙"而非"可正常耗尽的预算"——续轮唯一途径是 tool_use，而 tool_use 在最后一轮必被熔断，故不存在"预算正常耗尽"路径）。真正的"耗尽"形态是 420 行的被迫熔断（LLM 尚未 end_turn 即被切掉，pending 工具未执行）。
- **教学注意**: `docs/articles/03` 步骤 9 终止条件表中 "max_turns 到达" 一行描述为"兜底"，易误导读者以为存在正常耗尽路径；表述应以 420 行熔断为准，505 行为最后防线。
- **状态**: 不改代码，文档标记

### U4 — resume × 并发：中断时整组 pending 工具重跑

- **文件**: `loop.py:_execute_pending_tools` + `concurrency.py:execute_concurrent`
- **影响**: 崩溃发生在并发组执行中途时，事件只落了 tool_call 未落 tool_result，`resume()` 会把整组 pending 工具重跑。write_file 幂等无害，但 patch 会因 old_str 已被替换而报错，bash 有重复副作用风险。
- **修复代价**: 高——需 checkpoint 语义变更（执行前落 in-flight 标记，resume 识别中断组）
- **替代方案**: 工具执行前 checkpoint 已把重跑窗口缩到单组内；可接受
- **状态**: 等待设计

### U5 — eval harness workdir 跨进程共享，并行评测互删目录

- **文件**: `eval/harness.py:_set_workdir`（workdir = `base_dir/instance_id`，不按 cell 隔离）
- **影响**: 三个 cell 并行跑同一批 instances 时（2026-08-05 消融实测踩中两次）：进程 A 的 `_force_remove` / clone 会删除进程 B 正在使用的同一 workdir → `checkout failed: WinError 2` 或 clone 冲突。8 题 × 3 cell 中有 4 个 run 被污染需补跑。
- **修复**: 3 行——`_set_workdir` 的 workdir 路径加 cell 后缀（或 `_run_db_path` 同款 `instance_id__cell_label` 命名），manifest/DB 本就按 cell 隔离
- **状态**: **已修复（R8）**——workdir 与 `.restore_` 临时目录均按 `instance_id__cell_label` 隔离（`eval/harness.py:_set_workdir`），无 cell 参数调用保持原行为；`tests/test_eval_harness.py` 21 个用例全过

---

## 低优先级表（全部已处理）

| # | 领域 | 处理结果 |
|---|------|---------|
| 1 | stale_snip 多路径少回收 | C4 修复 |
| 2 | stale_snip 防御缺失 | malformed 输入，正常路径不可达 |
| 3 | truncate 消息时序 | = U1，等待重写 |
| 4 | truncate 原子性过宽 | 不存在实际问题 |
| 5 | anthropic codec 400 风险 | C2 修复 |
| 6 | truncate 性能 | R2 修复 |
| 7 | trajectory 事件噪声 | = U2，分析侧去重 |
| 8 | scripts 工具缺失 | 非代码缺陷 |
| 9 | concurrency scope Windows 路径 | R5 修复 + R6 加强（normcase 大小写归一化） |
| 10 | glob resolve 开销 | 可忽略（一次 stat 调用） |
| 11 | grep 递归深度 | R4 修复 |
| 12 | glob 路径解析 | 与 #10 同类，可忽略 |

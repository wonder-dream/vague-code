# Known Issues & Unfixed Bugs

Last updated: 2026-07-28

此文档记录项目当前已知问题状态。本会话已修复 11 项，6 项经分析非实际缺陷，2 项为代价高/分析侧处理。

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
| 9 | concurrency scope Windows 路径 | R5 修复 |
| 10 | glob resolve 开销 | 可忽略（一次 stat 调用） |
| 11 | grep 递归深度 | R4 修复 |
| 12 | glob 路径解析 | 与 #10 同类，可忽略 |

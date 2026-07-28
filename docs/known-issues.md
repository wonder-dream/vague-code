# Known Issues & Unfixed Bugs

Last updated: 2026-07-28

此文档记录三轮审计后确认但不急修的 LOW-MEDIUM 优先级问题，供后续迭代参考。

---

## `_extract_path` 只返回 `paths` 列表的第一个元素

- **文件**: `src/agent/context_compress.py:_extract_path`（`return str(raw[0])`）
- **场景**: `grep(input={"paths": ["a.py", "b.py"]})` 只追踪 `"a.py"`（`"b.py"` 的读取不会被 stale_snip 嗅探到）
- **修复代价**: 中等——需改 `_extract_path` 返回类型为 `list[str]` + `path_map` 构建逻辑
- **触发概率**: 低

---

## 消息中 `tool_use_id` 重复时静默覆盖

- **文件**: `context_compress.py:result_map[block.tool_use_id] = block`
- **影响**: malformed input，正常执行路径不可达

---

## truncate 尾部贪心导致新旧 pair 时序偏移

- **文件**: `context_compress.py:truncate`（tail prepend + standalone append）
- **修复代价**: 高——需重写 truncate 核心算法
- **替代**: per-message token 缓存可同时解决

---

## `_find_pairs` 对非 tool 的 assistant→user 对也做原子绑定

- **文件**: `context_compress.py:_find_pairs`
- **影响**: 纯文本轮次被成对丢弃，本可只丢 assistant 保留 user
- **修复代价**: 中等——加 `tool_only=False` 参数

---

## Anthropic 合并 assistant 后 thinking 块非首位

- **文件**: `codecs/anthropic.py:_merge_consecutive_same_role`
- **影响**: 400 错误。触发需 truncate 打乱顺序 + thinking 非首位，概率低

---

## truncate O(P×C) 性能瓶颈

- **文件**: `context_compress.py:truncate`——贪心每次 `count_tokens` 全量
- **修复**: per-message token 缓存，降为 O(C)
- **优先级**: WEEK 4 评测前

---

## resume 重复 compression 事件

- **文件**: `loop.py:finally` + `Agent.resume`
- **修复**: 不修代码，分析侧按 `(run_id, turn, layer)` 去重

---

## 缺少 `demo_week2_phase2.py` 演示脚本

- **文件**: `scripts/` 目录
- **修复**: WEEK 3/4 补充

---

## DeepSeek ThinkingBlock-only assistant 消息崩溃

- **文件**: `codecs/deepseek.py:_encode_assistant`（L82-83 continue 后 L91-92 抛出 ValueError）
- **场景**: LLM 响应仅含 reasoning_content（触及 max_tokens 时可能发生），`decode_response` 产生 `Message(content=[ThinkingBlock])`，下一轮编码时 ThinkingBlock 被 `continue` 跳过，因无 text/tool_calls 报错
- **影响**: resume 路径 `to_messages()` 重建后重新编码时崩溃
- **触发概率**: 低——仅当 `end_turn` 前模型触发 max_tokens 且响应只有 thinking 时

---

## `to_messages` 非确定性 system prompt 重建

- **文件**: `trajectory.py:205`——每次 `to_messages` 重新执行 `SystemPrompt(workdir).build()` 读取 `.xcode/rules.md`
- **影响**: 若 run 期间 rules.md 被修改，resume 使用不同 system prompt，评测 pass-to-pass 受外部变量污染

---

## `compress_chain` 异常吞噬后 over-budget 消息发送到 LLM

- **文件**: `loop.py:235-240`——`compress_chain` 异常被 catch 但 `messages` 保留未压缩版本，后续 LLM 调用用超标原文 → API 400
- **影响**: 仅当压缩层抛异常时触发（前序已加 try 保护，但 count_tokens 中 tiktoken 特殊 token 仍可能抛）

---

## 三层截断单位不一致（tools + loop + microcompact）

- **文件**: `tools.py:13`（bytes）→ `loop.py:371`（chars 50000）→ `microcompact:157`（chars 4000）
- **影响**: bash 工具输出先被 tools 层 51200 bytes 截断，再被 loop 层 50000 chars 截断。UTF-8 多字节字符使两阈值差异不可预测

---

## retry 事件的 token 估计缺 `skip_thinking`

- **文件**: `loop.py:298` + `retry.py:130-131`
- **影响**: DeepSeek 模型中 retry 事件 `estimated_input_tokens` 偏高（包含不会发送的 thinking）

---

## `max_turns` 判定偏移（resume vs non-resume 差 1）

- **文件**: `loop.py:329` vs `loop.py:407`
- **影响**: resume 比正常路径早一个 turn 停止

---

## checkpoint + resume 持久化窗口

- **文件**: `loop.py:336-337`（checkpoint 在 tool 执行前）+ `resume:412`（`_execute_pending_tools` 无 checkpoint）
- **影响**: resume 补执行的 tool 结果只有在 `_run_gen` 的 `finally` 才 persist，中间 crash 丢失

---

## `to_messages` 空 `task` 创建空 text 消息

- **文件**: `trajectory.py:208`——`Message(role="user", content=task)` 当 `task=""` 时创建 `TextBlock(text="")`
- **影响**: 部分 LLM API 拒绝空 content 的 user 消息

---

## `flush_results` 无去重

- **文件**: `trajectory.py:194-197, 221-231`
- **影响**: resume + replay 场景下同一 `tool_use_id` 的 tool_result 可能出现两次

---

## Anthropic ThinkingBlock 静默丢弃

- **文件**: `codecs/anthropic.py:119-125`——无 signature 的 ThinkingBlock 被静默跳过
- **影响**: 正常路径 signature 不为 None，不影响。静默丢弃无告警

---

## 本批修复（2026-07-28 压测）

| 修复 | Bug | 文件 | commit |
|------|-----|------|--------|
| 超时异常变量污染（B5） | 🔴 | `concurrency.py` | `b986b0f` |
| 冲突感知组跳过（B6） | 🟡 | `concurrency.py` | `b986b0f` |
| dict-based lookup 代替 zip（B1） | 🔴 | `loop.py` | `b986b0f` |
| Bash 超时输出保留（B8） | 🔴 | `tools.py` | `b986b0f` |
| 空 rule pattern 跳过（B13） | 🔴 | `permission.py` | `bf28a55` |
| cp/mv 安全分类（B14） | 🟡 | `permission.py` | `bf28a55` |
| LIKE 转义顺序（B18） | 🟡 | `memory.py` | `be5435a` |
| 并发安全连接（B19） | 🟢 | `memory.py` | `be5435a` |
| 时间衰减排序（B20） | 🟢 | `memory.py` | `be5435a` |
| from_db 恢复 memory 配置（B21） | 🟡 | `trajectory.py` | `be5435a` |
| str() 替代 repr() fallback（B22） | 🟢 | `trajectory.py` | `be5435a` |
| run_end 错误分类保持（B2） | 🟡 | `loop.py` | `e9749a4` |
| workdir 属性化（B3） | 🟡 | `loop.py` | `e9749a4` |
| tool 后 checkpoint（B4） | 🟡 | `loop.py` | `e9749a4` |
| chcp & 代替 &&（B9） | 🟡 | `tools.py` | `e9749a4` |
| retry 最小延迟 0.5s（B23） | 🟢 | `retry.py` | `e9749a4` |

## 历史修复记录

| 修复 | 文件 | commit |
|------|------|--------|
| truncate LayerReport 补 `skip_thinking` | `context_compress.py` | `b1eb29c` |
| auto_compact 死 else → assert | `context_compress.py` | `b1eb29c` |
| 冗余 `if msgs` → `len(msgs) > 0` | `context_compress.py` | `b1eb29c` |
| microcompact 未用 asst_idx → `_` | `context_compress.py` | `b1eb29c` |
| `meta.get("compacted")` → `"compacted" not in` | `context_compress.py` | `b1eb29c` |
| `_split_lines` 内联 | `context_compress.py` | `b1eb29c` |
| AnthropicBackend 默认 model 改 `claude-sonnet-4-5` | `backend.py` | `b1eb29c` |
| microcompact `new_blocks` 冗余删除 | `context_compress.py` | `b1e044f` |
| compacted head/tail 行数用动态值 | `context_compress.py` | `b1e044f` |
| `CONTEXT_WINDOWS` 加 `claude-sonnet-4-5` | `context_tokens.py` | `b1e044f` |
| budget 事件加 `affected`/`skip_thinking` | `loop.py` | `b1e044f` |
| 4 处 `from copy import deepcopy` 移到模块顶 | `context_compress.py` | `b1e044f` |
| `loop.py` 重复 `_dump_json` 删除 | `loop.py` | `b1e044f` |
| `test_auto_compact.py:54` `or` 改 `and` | `test_auto_compact.py` | `b1e044f` |
| `_pattern_prefix` 根目录 `"."` → `""` | `concurrency.py` | `cffccce` |
| grep 默认 `"."` → `""` | `concurrency.py` | `cffccce` |
| `_scopes_conflict` 加路径边界检查 `_path_under` | `concurrency.py` | `cffccce` |
| 加 `_CONCURRENT_TIMEOUT` 超时保护 | `concurrency.py` | `cffccce` |
| loop 并发分支加 try/except | `loop.py` | `f2cd058` |
| 加并发集成测试（2 个） | `test_agent_loop.py` | `f2cd058` |
| 边界+异常+传播测试（5 个） | `test_concurrency.py` | `cffccce`+`f2cd058` |

## 剩余低优先级项

| # | 领域 | 类型 | 触发条件 | 修复代价 | 优先级 |
|---|------|------|----------|----------|--------|
| 1 | stale_snip | 功能少回收 | 多路径 grep | 中 | 低 |
| 2 | stale_snip | 防御缺失 | malformed 输入 | 低 | 低 |
| 3 | truncate | 消息时序 | budget 紧张 | 高 | 低 |
| 4 | truncate | 原子性过宽 | 纯文本轮次 | 中 | 低 |
| 5 | anthropic codec | 400 风险 | assistant 合并+thinking | 中 | 中 |
| 6 | truncate | 性能 | 超长会话 | 中 | WEEK 4 前 |
| 7 | trajectory | 事件噪声 | resume 中断 | 零（文档） | 低 |
| 8 | scripts | 工具缺失 | 评测 | 中 | WEEK 3/4 |
| 9 | concurrency scope | Windows 路径 | `_pattern_prefix` 不处理 `\\` 分隔符 | 低 | 低 |
| 10 | glob | 性能 | `path.resolve()` 每次匹配 stat 调用 | 低 | 低 |
| 11 | patch | 防御缺失 | `.read_text()` 无 `UnicodeDecodeError` 保护 | 低 | 低 |
| 12 | grep | 性能 | `rglob` 无限递归深度（`node_modules` 等） | 低 | 低 |

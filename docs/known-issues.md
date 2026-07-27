# Known Issues & Unfixed Bugs

Last updated: 2026-07-27

此文档记录两轮压力审计后确认但不急修的 LOW 优先级问题，供后续迭代参考。

---

## 1. `_extract_path` 只返回 `paths` 列表的第一个元素

- **文件**: `src/agent/context_compress.py:_extract_path`
- **行号**: `return str(raw[0])`（单一路径提取）
- **场景**: `grep(input={"paths": ["a.py", "b.py"]})` → 只追踪 `"a.py"`，`"b.py"` 的读取不会被 stale_snip 嗅探到
- **影响**: 少回收 token，不影响正确性。多路径 `grep` 用例较少
- **修复代价**: 中等——需把 `_extract_path` 返回类型从 `str | None` 改为 `list[str]`，stale_snip 内部 `path_map` 构建逻辑也要相应修改
- **触发概率**: 低——`grep` 工具使用 `paths` 多文件搜索的场景在 coding agent 中少见

---

## 2. 消息中 `tool_use_id` 重复时静默覆盖

- **文件**: `src/agent/context_compress.py:stale_snip`
- **行号**: `result_map[block.tool_use_id] = block`
- **场景**: 单条 user 消息中有两个 `ToolResultBlock` 使用相同的 `tool_use_id`（数据异常）
- **影响**: 后一个覆盖前一个，stale 判定仅基于最后出现的 block
- **修复代价**: 极低（加 warn 或 dedup），但属 malformed input，正常执行路径不可达
- **触发概率**: 理论上的错误，agent 消息构造不会产生重复 id

---

## 3. truncate 尾部贪心导致新旧 pair 时序偏移

- **文件**: `src/agent/context_compress.py:truncate`
- **场景**: 新 pair（最近轮）从尾部 prepend 到 tail_messages，旧 pair 和 standalone 消息被 append 在后面。最终输出顺序为 `[prefix, 新pair, 旧pair/standalone]`，vs 原文序 `[prefix, 旧pair/standalone, 新pair]`
- **影响**: 消息顺序不完全保持时间序，但 LLM 从消息内容理解上下文，不产生非法请求
- **修复代价**: 高——需要重写 truncate 核心算法为"维护全量有序索引"方式，涉及 marker 循环和 standalone 收集逻辑全部调整
- **替代方案**: per-message token 缓存（性能优化项）可同时解决此问题

---

## 4. `_find_pairs` 捕获非 tool 的 assistant→user 对（过于宽泛）

- **文件**: `src/agent/context_compress.py:_find_pairs`
- **场景**: 任何相邻 `assistant→user`（包括纯文本对话轮次）被视作"pair"绑定原子性
- **影响**: truncation 时纯文本 assistant+user 被一起丢弃，本可以只丢弃 assistant 保留 user 响应
- **修复代价**: 中等——给 `_find_pairs` 加 `tool_only=False` 参数，`truncate` 调用时传 `tool_only=True`。副作用：需修改 `stale_snip` 和 `microcompact` 调用处，需验证所有路径的配对语义一致
- **收益**: 极小——纯文本轮次被成对丢弃的概率不高

---

## 5. Anthropic 合并 assistant 后 thinking 块非首位

- **文件**: `src/agent/codecs/anthropic.py:_merge_consecutive_same_role` / `_encode_assistant`
- **场景**: 两个 consecutive assistant 合并后，后者的 thinking block 排在前面消息的 text/tool_use 之后 → Anthropic API 要求 thinking 必须是 assistant content 的第一个 block
- **影响**: 400 错误（"thinking block must be first block in content"）。触发条件：`_merge_consecutive_same_role` 合并了两个 assistant 且后者有 thinking 块
- **触发概率**: 低——需要合并 consecutive assistant（仅当 truncate 打乱顺序或手动构造消息序列），且 assistant 消息中同时有 text+thinking+thinking 排序异位
- **修复代价**: 中等——`_encode_assistant` 编码前稳定排序 blocks（thinking→text→tool_use），或 `_merge_consecutive_same_role` 合并后重排
- **临时缓解**: 当前生产路径下（DeepSeek 主用），此问题不触发

---

## 6. truncate O(P×C) 性能瓶颈

- **文件**: `src/agent/context_compress.py:truncate`
- **场景**: truncate 贪心循环每迭代一次就全量 `count_tokens` 一次，总复杂度 O(P × C)，P=200 时最坏 ~25M tokens 编码 ≈ 25-50s
- **影响**: budget 紧张的超长会话压缩可耗时数秒
- **修复方案**: per-message token 缓存——压缩链入口一次性算出各消息的 token 数 O(C)，truncate 改为后缀累加 O(N)
- **优先级**: WEEK 4 评测前处理（不影响正确性，但污染端到端延迟因变量）

---

## 7. resume 重复 compression 事件

- **文件**: `src/agent/loop.py:finally` + `Agent.resume`
- **场景**: 中断发生在流式阶段且 `finally: self._persist` 成功执行时，部分 compression 事件落库后又被 resume 重复 emit → 同 turn 同 layer 两条事件
- **影响**: 事件流噪声，`to_messages()` 忽略 `compression` 事件所以不影响消息重建。M2 离线分析按 `(run_id, turn, layer)` 去重即可
- **修复建议**: 不修代码，分析侧做去重。事件 payload 中 `ts` 时间戳可用于保留最新的一条

---

## 8. 缺少 `demo_week2_phase2.py` 演示脚本

- **文件**: `scripts/demo_week2_phase2.py`（不存在）
- **场景**: M2 里程碑要求"记录压缩前后 token 数据"，当前无自动化演示脚本
- **依赖**: 需要确认修复 1a（层间 token 计数口径统一）和 6（disabled 基线事件）已就位——已在 Commit `3dfc7a6` 和 `4d5c59f` 中修复
- **修复计划**: WEEK 3 或 WEEK 4 阶段补充，功能：
  1. 跑（或加载）一条 30+ 轮轨迹
  2. 从 SQLite 事件流聚合各层 token 回收
  3. 输出每轮 util 曲线、压缩率、各层贡献
  4. enabled/disabled 对比

---

## 未修复项汇总

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

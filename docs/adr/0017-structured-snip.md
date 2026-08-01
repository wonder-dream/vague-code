---
status: accepted
date: 2026-08-01
---

# 0017: 轨迹驱动的结构化压缩层（structured_snip）

## 背景

原压缩管线：`stale_snip（零成本、精准）→ microcompact（零成本、启发式）→ auto_compact（LLM 成本高）→ truncation（盲砍）`。

消融数据显示 auto_compact 对短会话（<30 turn）负收益（76% pass rate vs 83% 不开压缩）。根源：`compress_chain` 输入是 `messages → messages` 纯函数，完全不知道轨迹事件里已有的结构化信息——哪些文件被读了、哪些被改了、测试跑没跑过。这些信息**在轨迹事件里已全部结构化存储**，零推理成本，但压缩管线完全没用。

## 决策

在 microcompact 之后、auto_compact 之前插一层 `structured_snip`：

```
stale_snip → microcompact → structured_snip → auto_compact → truncation
```

1. **零 LLM 成本**——纯规则匹配，只做事件流查询 + 消息替换
2. **轨迹事件为输入源**——`compress_chain` 签名扩展 `events` 参数，`loop.py` 传入 `traj.events`
3. **闭合子任务识别**——从最后一个成功的 bash（exit 0 且 is_error=False）反向追溯，到最近的探索工具（read/grep/glob）为止
4. **阈值定位**——`structured_snip_threshold=0.65`（介于 microcompact 0.5 与 auto_compact 0.85 之间），中负载时截住、避免走到 LLM 摘要

## 约束

1. **纯函数**——输入 `(messages, events)` → 输出 `(messages, report)`，不写数据库
2. **不破坏配对**——替换以整对（assistant+user）为单位，tool_use/tool_result 配对不变
3. **向后兼容**——`events=None` 时 structured_snip 直通，不改变既有调用方
4. **保留原文指针**——被压缩的原文通过 `meta["compacted_by"]` + `meta["turn_range"]` 标记，可从 trajectory 恢复

## 架构

```python
def _detect_subtasks(events) -> list[_Subtask]:
    # 子任务 = {start_turn, end_turn, tool_use_ids}
    # 从成功 bash 反向追溯，到最近探索工具

def structured_snip(messages, events=None, keep_recent=3, ...) -> (messages, LayerReport):
    # 最近 keep_recent 个子任务豁免，旧子任务替换为结构化摘要
```

摘要模板（从事件 payload 提取，不含 UUID/timestamp）：

```
[已完成子任务 (turn 0-2)]
  read_file: stats.py
  patch: stats.py
  bash: pytest tests/test_stats.py
```

## Consequences

- 预期将压缩 ON 的 pass rate 拉回接近基线（83%）
- auto_compact 的 LLM 调用（每次 ~2-5K input tokens）在多数情况下可被省掉
- 事件流扫描 O(N)（N = tool_call 事件数），通常 < 50 个事件，延迟可忽略
- 关联实现：`docs/plans/0013-structured-snip.md`

# 0008: 四层压缩流水线

实现 stale_snip → microcompact → auto_compact → truncation 四层压缩，配置化门控，可观测事件。

---

## 设计原则

- **纯函数**：压缩 `messages → messages`，不改 trajectory，不写数据库
- **按精准度降序**：stale_snip 零损失 → microcompact 启发式头尾保留 → auto_compact LLM 摘要 → truncation 硬截断
- **可观测**：每层产出 `LayerReport` 落 `EventType.compression` 事件
- **ThinkingBlock 不纳入预算**：DeepSeek 编码时丢弃 thinking，compress_chain 内 `count_tokens(..., skip_thinking=True)`

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `src/agent/config.py` | 改：加 `CompressionConfig` |
| 2 | `src/agent/context_tokens.py` | 改：`count_tokens` 加 `skip_thinking` |
| 3 | `src/agent/context_compress.py` | **新建**：四层 + compress_chain |
| 4 | `src/agent/context.py` | 改：re-export `compress_chain` |
| 5 | `src/agent/loop.py` | 改：预算处接入 compress_chain |
| 6 | `tests/test_stale_snip.py` | **新建** |
| 7 | `tests/test_microcompact.py` | **新建** |
| 8 | `tests/test_auto_compact.py` | **新建** |
| 9 | `tests/test_truncate.py` | **新建** |
| 10 | `tests/test_compress_chain.py` | **新建** |
| 11 | `tests/test_loop_compression.py` | **新建** |

---

## 步骤 1：`config.py`

在 `TransportConfig` 之后、`AgentConfig` 之前插入 `CompressionConfig`：

```python
@dataclass
class CompressionConfig:
    enabled: bool = True
    microcompact_threshold: float = 0.5
    microcompact_max_chars: int = 4000
    microcompact_keep_recent: int = 3
    auto_compact_threshold: float = 0.85
    auto_compact_keep_turns: int = 4
    stale_snip_keep_recent: int = 3
```

`AgentConfig` 新增字段：

```python
@dataclass
class AgentConfig:
    ...
    compression: CompressionConfig = field(default_factory=CompressionConfig)
```

`AgentConfig.__post_init__` 无需额外校验——字段类型系统已有保障。

---

## 步骤 2：`context_tokens.py`

`count_tokens` 加 `skip_thinking: bool = False` 参数。当 `skip_thinking=True` 时，`ThinkingBlock` 跳过。

```python
def count_tokens(
    messages: list,
    tools: list[ToolSpec] | None = None,
    skip_thinking: bool = False,
) -> int:
    enc = _get_enc()
    if enc is not None:
        return _count_precise(messages, tools, enc, skip_thinking)
    return _count_rough(messages, tools, skip_thinking)


def _count_precise(messages, tools, enc, skip_thinking):
    total = 0
    for msg in messages:
        blocks = msg.content if isinstance(msg.content, list) else []
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(enc.encode(block.text))
            elif isinstance(block, ThinkingBlock):
                if not skip_thinking:
                    total += len(enc.encode(block.text))
            elif isinstance(block, ToolUseBlock):
                total += len(enc.encode(block.name))
                total += len(enc.encode(json.dumps(block.input)))
            elif isinstance(block, ToolResultBlock):
                total += len(enc.encode(block.content))
    if tools:
        for t in tools:
            total += len(enc.encode(t.description))
            total += len(enc.encode(json.dumps(t.parameters)))
    return total


def _count_rough(messages, tools, skip_thinking):
    # 同上，4 倍除法估算
```

需 import `ThinkingBlock`。

---

## 步骤 3：`context_compress.py`（新建）

### 3.1 数据结构

```python
from __future__ import annotations

from dataclasses import dataclass, field
from src.agent.ir import (
    Block, Message, TextBlock, ToolResultBlock, ToolUseBlock,
    ThinkingBlock,
)
from src.agent.context_tokens import count_tokens


@dataclass
class LayerReport:
    layer: str
    before_tokens: int
    after_tokens: int
    affected: int
    skip_thinking: bool = True
    detail: dict = field(default_factory=dict)
```

### 3.2 `stale_snip`

```python
def stale_snip(
    messages: list[Message],
    keep_recent: int = 3,
) -> tuple[list[Message], LayerReport]:
```

逻辑：
1. 倒序遍历 messages，找到所有 `ToolUseBlock`（取 `name` + `input` 中的 path）+ 紧随其后的 `ToolResultBlock`
2. 建立 `path → [list of (index_in_messages, ToolUseBlock, ToolResultBlock)]` 映射
3. 每个 path 的列表，除最后一条外，标记前面的 ToolResultBlock 为 stale
4. 保护最近 `keep_recent` 条配对不处理
5. 替换 content 为 `"[stale: superseded by later read of {path}]"`，设置 `meta["stale"] = True`
6. `is_error=True` 的跳过

**判定为"读取类工具"**：`name in ("read", "glob", "grep")`，从 `input` 取 `"path"` 键精确匹配。

**不跨消息配对**：连续的 `[assistant(ToolUse)] + [user(ToolResult)]` 为一对；对之间的索引直接按 messages 列表顺序推断。

### 3.3 `microcompact`

```python
def microcompact(
    messages: list[Message],
    max_chars: int = 4000,
    keep_recent: int = 3,
) -> tuple[list[Message], LayerReport]:
```

逻辑：
1. 倒序遍历，跳过最近 `keep_recent` 条 assistant+user 配对
2. 对 ToolResultBlock.content 长度 > `max_chars` 且 `meta.get("stale")` 不是 True 的做折叠
3. 折叠格式：
   ```
   [compacted: {total} chars, {n} lines, tool={name}]
   --- head ({head_n} lines) ---
   {head}
   --- tail ({tail_n} lines) ---
   {tail}
   ```
4. head 20 行，tail 10 行（硬编码）
5. `meta["compacted"] = {"original_chars": len(content), "tool_use_id": tid}`

### 3.4 `auto_compact`

```python
def auto_compact(
    messages: list[Message],
    backend,
    model: str,
    keep_turns: int = 4,
) -> tuple[list[Message], LayerReport]:
```

逻辑：
1. 从前往后扫，排除 system message
2. 保留：system message + 最新 `keep_turns` 轮（按 assistant/user 对计数）
3. 中间部分序列化为摘要请求：
   ```python
   summary_prompt = (
       "Summarize the following coding session concisously.\n"
       "Include: user's original task, what has been done, "
       "key file paths and changes, pending work, errors/blockers.\n"
   )
   # 将待摘要部分 each Message → "user:<content>" or "assistant:<content>"
   lines = []
   for msg in to_summarize:
       text_content = " ".join(b.text for b in msg.content if isinstance(b, TextBlock))
       lines.append(f"{msg.role}: {text_content}")
   request_text = summary_prompt + "\n---\n" + "\n".join(lines)
   ```
4. 调用 `backend.complete(summary_messages, tools=None, config={"model": model, "stream": False})`
5. 重建：`[system, Message("user", f"[Session summary]\n{summary_text}"), ...keep_turns 原文]`
6. 摘要失败 → 返回 `(messages, LayerReport(layer="auto_compact", affected=0, ...detail={"error": str(e)}))`，上层继续 truncation

### 3.5 `truncate`

```python
def truncate(
    messages: list[Message],
    budget: int,
    tools: list | None = None,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
```

逻辑：
1. 走一遍 `count_tokens` 如果已符合 → 直通（affected=0）
2. 保留 system（索引 0）+ 首条 user（索引 1）永远不删
3. 从索引 `len(messages)-1` 往前贪心回填，每次取一条消息
   - 当取到 assistant → 连带它之后的 user/tool_result 一起拿（必须有后续配对）
   - 当取到 user → 单独拿（纯用户消息不含 tool_result）
4. 装填到新列表，每次加后 check `count_tokens`；即将超 budget 时停止
5. 在保留的首条 user 之后插标记：
   `Message("user", content=[TextBlock(f"[truncated: dropped {dropped_count} messages to fit token budget"])])`

### 3.6 `compress_chain`（串联）

```python
def compress_chain(
    messages: list[Message],
    tools: list | None,
    cfg: CompressionConfig,
    budget: int,
    backend=None,
    model: str = "",
) -> tuple[list[Message], list[LayerReport]]:

    total = count_tokens(messages, tools, skip_thinking=True)
    reports: list[LayerReport] = []

    # 层 1（无条件执行）
    messages, report = stale_snip(messages, cfg.stale_snip_keep_recent)
    reports.append(report)

    # 层 2（util > microcompact_threshold）
    new_total = count_tokens(messages, tools, skip_thinking=True)
    if new_total > budget * cfg.microcompact_threshold:
        messages, report = microcompact(messages, cfg.microcompact_max_chars, cfg.microcompact_keep_recent)
        reports.append(report)
        new_total = count_tokens(messages, tools, skip_thinking=True)

    # 层 3（util > auto_compact_threshold 且有 backend）
    if backend is not None and new_total > budget * cfg.auto_compact_threshold:
        messages, report = auto_compact(messages, backend, model, cfg.auto_compact_keep_turns)
        reports.append(report)
        new_total = count_tokens(messages, tools, skip_thinking=True)

    # 层 4
    if new_total > budget:
        messages, report = truncate(messages, budget, tools, skip_thinking=True)
        reports.append(report)

    return messages, reports
```

### 3.7 辅助：`_find_read_tool_pairs`（stale_snip 内部用）

```python
def _find_read_tool_pairs(
    messages: list[Message],
) -> list[tuple[int, int, str, int]]:
    """
    返回 [(assistant_idx, user_idx, path, chars)] 列表。
    解析 messages 中连续 assistant→user 对中的 read 类工具调用。
    """
```

README 工具集合：

```python
_READ_TOOLS = frozenset({"read", "glob", "grep"})
```

### 3.8 辅助：`_count_recent_pairs`（保护免折叠轮数）

```python
def _count_recent_pairs(messages: list[Message], n: int) -> int:
    """从尾往前数 n 对 assistant+user 的索引，返回受保护的起始位置。"""
```

---

## 步骤 4：`context.py`

添加单行 re-export：

```python
from src.agent.context_compress import compress_chain  # noqa: F401
```

---

## 步骤 5：`loop.py`

将 `loop.py:229-241` 的简易 budget 记账块替换为：

```python
total = count_tokens(messages, self._tool_specs)
budget = compute_budget(self.config.model)
cfg = self.config.compression

if cfg.enabled:
    messages, reports = compress_chain(
        messages, self._tool_specs, cfg, budget,
        backend=self.backend, model=self.config.model)
    for r in reports:
        traj.emit(EventType.compression, turn=turn, payload={
            "layer": r.layer,
            "before_tokens": r.before_tokens,
            "after_tokens": r.after_tokens,
            "affected": r.affected,
            "skip_thinking": r.skip_thinking,
            **({"detail": r.detail} if r.detail else {}),
        })
else:
    # 压缩关闭时仍 emit budget 事件保持日志连续性
    traj.emit(EventType.compression, turn=turn, payload={
        "layer": "budget",
        "before_tokens": total,
        "after_tokens": total,
        "budget": budget,
        "utilization": round(total / budget, 4) if budget > 0 else 0.0,
        "tools_tokens": count_tokens([], self._tool_specs),
        "tool_count": len(self._tool_specs),
    })
```

注意 `_run_gen` 的 `messages` 参数是局部变量，直接 `messages = ...` 即可——resume 路径同样经过 `_run_gen`，自然继承。

在 `_run_gen` 中访问 `self.config.compression` 而不注入新的构造参数——loop.py 已有 `self.config`。

---

## 步骤 6~11：测试

### 通用测试夹具

```python
from src.agent.ir import (
    Block, Message, TextBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock,
    StopReason, ModelResponse, NormalizedUsage,
)
from src.agent.config import CompressionConfig
```

### `test_stale_snip.py`

| 函数 | 验证点 |
|------|--------|
| `test_single_read_no_snip` | 单次 read，无 snip |
| `test_same_path_twice` | 同一 path read 两次，第一条 content 被替换 |
| `test_keep_recent_exempt` | 最近 N 条豁免 |
| `test_error_result_skipped` | is_error 跳过 |
| `test_different_paths_not_affected` | 不同 path 互不影响 |
| `test_tool_pair_integrity` | 只改 content，不改结构 |
| `test_tokens_decreased` | 压缩后 count_tokens 减少 |

构造方法：直接构造 `[Message("assistant", [ToolUseBlock(...)]), Message("user", [ToolResultBlock(...)])]` 列表，传入 `stale_snip`。

### `test_microcompact.py`

| 函数 | 验证点 |
|------|--------|
| `test_short_content_unchanged` | 未超阈值不变 |
| `test_long_content_compacted` | 超阈值折叠 + head/tail 结构 |
| `test_meta_pointer_set` | `meta["compacted"]` 含 original_chars |
| `test_stale_blocks_skipped` | 已 stale 不重复折叠 |
| `test_keep_recent_immune` | 最近 N 条不折叠 |
| `test_tokens_decreased` | token 减少 |

### `test_auto_compact.py`

| 函数 | 验证点 |
|------|--------|
| `test_summary_request_contains_history` | FakeBackend 收到的 summary prompt 含历史文本 |
| `test_rebuild_structure` | 重建后含 system + summary + keep_turns 原文 |
| `test_system_preserved` | system message 不被摘要掉 |
| `test_failure_graceful` | backend 抛异常 → affected=0 的 report，原 messages 不变 |

FakeBackend 这里的 `complete` 可以返回一个固定摘要文本或抛出异常，验证行为。

### `test_truncate.py`

| 函数 | 验证点 |
|------|--------|
| `test_under_budget_noop` | 已低于 budget，不做操作 |
| `test_budget_respected` | 返回后 count_tokens 不超过 budget |
| `test_system_and_task_preserved` | system + 首条 user 始终存在 |
| `test_tool_pair_atomicity` | assistant(ToolUse) 不会残缺其后的 user(ToolResult) |
| `test_truncation_marker` | 放弃消息时插入标记 message |

### `test_compress_chain.py`

| 函数 | 验证点 |
|------|--------|
| `test_low_utilization_stale_only` | budget*0.3 → 只有 stale_snip 触发 |
| `test_medium_utilization_stale_micro` | 0.6 触发 → 层 1+2 |
| `test_high_utilization_all` | 0.9 触发 → 四层 |
| `test_enabled_false_noop` | `CompressionConfig(enabled=False)` → 直通 |
| `test_chain_order_preserved` | 四层按顺序执行，前面的 output 是后面的 input |
| `test_reports_chain` | reports 列表顺序 = stale→micro→auto→truncate |

### `test_loop_compression.py`

集成测试，用 FakeBackend 模拟 30+ 轮长会话：

| 函数 | 验证点 |
|------|--------|
| `test_long_session_compression_events` | 轨迹中 compression 事件齐全 |
| `test_task_goal_survives` | 最终回复提及原始任务关键词（compress 不丢目标） |
| `test_message_count_bounded` | 30 轮后消息数不线性增长（被压缩控制） |

构造方法：`_tool_use_response` + `_text_response` 交替，每次 tool_results 返回不同文件路径 + 长内容，模拟长时间会话。

---

## 执行顺序

1. `config.py` — CompressionConfig
2. `context_tokens.py` — skip_thinking
3. `context_compress.py` — 四层 + compress_chain
4. `context.py` — re-export
5. `loop.py` — 集成
6. 测试 — 逐层单测 + 链测试 + 集成测试

每步 `pytest tests/` 确保不打破现有测试。最后全量 `ruff + mypy + pytest`。

---

## 验收标准

- [x] `pytest tests/` 全量通过（含现有测试）
- [x] `ruff check src/` 无新增告警
- [x] `mypy src/` 无新增类型错误
- [ ] demo 脚本能跑出压缩前后 token 对比数字
- [ ] `scripts/demo_week2_phase2.py` — 可选：用长会话验证 M2

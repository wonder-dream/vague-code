from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum

from vague_code.agent.context_tokens import count_tokens
from vague_code.agent.ir import (
    Message,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from vague_code.agent.trust import mark_untrusted


@dataclass
class LayerReport:
    layer: str
    before_tokens: int
    after_tokens: int
    affected: int
    skip_thinking: bool = True
    detail: dict = field(default_factory=dict)


_READ_TOOLS = frozenset({"read", "read_file", "glob", "grep"})

_SUBTASK_ACTION_TOOLS = frozenset({"read_file", "write_file", "patch", "glob", "grep"})

_HEAD_LINES = 20
_TAIL_LINES = 10


# ── Helper: find assistant+user pair positions ─────────────────────────────

def _find_pairs(messages: list[Message]) -> list[tuple[int, int]]:
    """Return [(assistant_idx, user_idx), ...] for consecutive assistant→user pairs."""
    pairs: list[tuple[int, int]] = []
    i = 0
    while i < len(messages) - 1:
        if messages[i].role == "assistant" and messages[i + 1].role == "user":
            pairs.append((i, i + 1))
            i += 2
        else:
            i += 1
    return pairs


# ── Helper: extract path from read-tool input ──────────────────────────────

def _extract_paths(tool_block: ToolUseBlock) -> list[str]:
    if tool_block.name not in _READ_TOOLS:
        return []
    raw = None
    for key in ("path", "paths", "pattern"):
        v = tool_block.input.get(key)
        if v is not None:
            raw = v
            break
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return raw
    return []


# ── Layer 1: stale_snip ────────────────────────────────────────────────────

def stale_snip(
    messages: list[Message],
    keep_recent: int = 3,
    tools: list | None = None,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Replace ToolResultBlocks superseded by later same-path reads with a stale placeholder."""
    msgs = deepcopy(messages)
    before = count_tokens(msgs, tools, skip_thinking=skip_thinking)

    pairs = _find_pairs(msgs)
    eligible = pairs[:max(0, len(pairs) - keep_recent)] if keep_recent > 0 else pairs

    # Build (tool_name, path)→list of (user_idx, block_idx, ToolResultBlock)
    # Each tool type only competes with same tool type (e.g. read↔read, not read↔grep)
    path_map: dict[tuple[str, str], list[tuple[int, int, ToolResultBlock]]] = {}

    for asst_idx, user_idx in eligible:
        asst_msg = msgs[asst_idx]
        user_msg = msgs[user_idx]

        # Find read-tool calls in this assistant message
        tool_blocks: list[tuple[int, ToolUseBlock]] = []
        for bi, block in enumerate(asst_msg.content):
            if isinstance(block, ToolUseBlock) and block.name in _READ_TOOLS:
                tool_blocks.append((bi, block))
        if not tool_blocks:
            continue

        # Index ToolResultBlocks in the user message by tool_use_id
        result_map: dict[str, ToolResultBlock] = {}
        block_indices: dict[str, int] = {}
        for bi, block in enumerate(user_msg.content):
            if isinstance(block, ToolResultBlock):
                if block.meta.get("stale"):
                    continue
                result_map[block.tool_use_id] = block
                block_indices[block.tool_use_id] = bi

        for bi, tool_block in tool_blocks:
            paths = _extract_paths(tool_block)
            if not paths:
                continue
            result_block = result_map.get(tool_block.id)
            if result_block is None or result_block.is_error:
                continue
            for path in paths:
                path_map.setdefault((tool_block.name, path), []).append((user_idx, block_indices[tool_block.id], result_block))

    affected = 0
    for (tool_name, path), entries in path_map.items():
        if len(entries) <= 1:
            continue
        for _, _, result_block in entries[:-1]:
            result_block.meta["stale"] = True
            result_block.meta["original_stale_content"] = result_block.content
            result_block.content = f"[已过期: 被后续 {tool_name} 的 {path} 覆盖]"
            affected += 1

    after = count_tokens(msgs, tools, skip_thinking=skip_thinking)
    return msgs, LayerReport(
        layer="stale_snip",
        skip_thinking=skip_thinking,
        before_tokens=before,
        after_tokens=after,
        affected=affected,
        detail={"stale_groups": sum(1 for v in path_map.values() if len(v) > 1)},
    )


# ── Layer 2: microcompact ──────────────────────────────────────────────────

def _head_tail(content: str, head_n: int, tail_n: int) -> tuple[str, str, int]:
    lines = content.splitlines(keepends=True)
    n = len(lines)
    if head_n + tail_n >= n:
        return "".join(lines), "", n
    head = "".join(lines[:head_n])
    tail = "".join(lines[-tail_n:]) if tail_n > 0 else ""
    return head, tail, n


def microcompact(
    messages: list[Message],
    max_chars: int = 4000,
    keep_recent: int = 3,
    tools: list | None = None,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Compact long ToolResultBlock content to head+tail summary."""
    msgs = deepcopy(messages)
    before = count_tokens(msgs, tools, skip_thinking=skip_thinking)

    pairs = _find_pairs(msgs)
    eligible = pairs[:max(0, len(pairs) - keep_recent)] if keep_recent > 0 else pairs
    affected = 0

    for _, user_idx in eligible:
        msg = msgs[user_idx]
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and not block.is_error:
                if (
                    len(block.content) > max_chars
                    and not block.meta.get("stale")
                    and "compacted" not in block.meta
                ):
                    head, tail, total_lines = _head_tail(block.content, _HEAD_LINES, _TAIL_LINES)
                    head_n = len(head.splitlines(keepends=True))
                    tail_n = len(tail.splitlines(keepends=True)) if tail else 0

                    if head_n + tail_n >= total_lines and len(block.content) > max_chars:
                        half = max_chars // 2
                        compacted = (
                            f"[已压缩: {len(block.content)} 字符, {total_lines} 行]"
                            f"\n{block.content[:half]}"
                            f"\n...[{total_lines} 行, 共 {len(block.content)} 字符]..."
                            f"\n{block.content[-half:]}"
                        )
                    else:
                        compacted = (
                            f"[已压缩: {len(block.content)} 字符, {total_lines} 行]"
                            f"\n--- head ({head_n} lines) ---\n{head}"
                            f"\n--- tail ({tail_n} lines) ---\n{tail}"
                        )
                    if len(compacted) < len(block.content):
                        block.meta["compacted"] = {
                            "original_chars": len(block.content),
                            "tool_use_id": block.tool_use_id,
                        }
                        block.content = compacted
                        affected += 1

    after = count_tokens(msgs, tools, skip_thinking=skip_thinking)
    return msgs, LayerReport(
        layer="microcompact",
        skip_thinking=skip_thinking,
        before_tokens=before,
        after_tokens=after,
        affected=affected,
    )


# ── Layer 2.5: structured_snip (trajectory-driven) ─────────────────────────

@dataclass
class _Subtask:
    """A closed read→modify→verify cycle identified from trajectory events."""

    start_turn: int
    end_turn: int
    tool_use_ids: set[str]


def _norm_event(ev) -> dict:
    """Normalize an Event (dataclass) or a plain dict into a uniform shape."""
    if isinstance(ev, dict):
        etype = ev.get("type")
        if isinstance(etype, Enum):
            etype = etype.value
        return {
            "type": str(etype),
            "turn": ev.get("turn"),
            "payload": ev.get("payload") or {},
        }
    etype = getattr(ev, "type", None)
    if isinstance(etype, Enum):
        etype = etype.value
    return {
        "type": str(etype),
        "turn": getattr(ev, "turn", None),
        "payload": getattr(ev, "payload", None) or {},
    }


def _tool_use_id(ev: dict) -> str | None:
    payload = ev["payload"]
    tid = payload.get("id") or payload.get("tool_use_id")
    return tid if isinstance(tid, str) else None


def _is_success_bash(ev: dict) -> bool:
    """True if this tool_result reflects a successful bash (exit 0, not an error)."""
    if ev["type"] != "tool_result":
        return False
    payload = ev["payload"]
    if payload.get("is_error"):
        return False
    content = payload.get("content") or ""
    return "退出码: 0" in content


def _is_action_tool(name: str) -> bool:
    return name in _SUBTASK_ACTION_TOOLS


def _detect_subtasks(events: list) -> list[_Subtask]:
    """Scan trajectory events and return closed read→modify→verify subtasks.

    A subtask opens at the first action tool after the previous successful bash
    (or the first turn), and closes at a successful bash (exit 0, not error).
    Turns opened but not yet closed are "in progress" and are excluded.
    """
    normalized = [_norm_event(ev) for ev in events]

    # tool_use_id -> (turn, name, input)
    calls: dict[str, tuple[int, str, dict]] = {}

    # per-turn: which action tools ran, and did a successful bash occur
    turn_actions: dict[int, set[str]] = {}
    turn_success_bash: set[int] = set()

    for ev in normalized:
        tid = _tool_use_id(ev)
        if not tid:
            continue
        turn = ev["turn"]
        if turn is None:
            continue
        if ev["type"] == "tool_call":
            name = ev["payload"].get("name") or ""
            calls[tid] = (turn, name, ev["payload"].get("input") or {})
        elif ev["type"] == "tool_result":
            if tid in calls:
                turn, name, _inp = calls[tid]
                if _is_action_tool(name):
                    turn_actions.setdefault(turn, set()).add(name)
                if _is_success_bash(ev) and name == "bash":
                    turn_success_bash.add(turn)

    if not turn_actions:
        return []

    subtasks: list[_Subtask] = []
    work_start: int | None = None

    for turn in sorted(set(list(turn_actions.keys()) + list(turn_success_bash))):
        if turn in turn_actions and work_start is None:
            work_start = turn
        if turn in turn_success_bash:
            if work_start is not None:
                subtask_ids = {
                    tid for tid, (t, _n, _i) in calls.items() if work_start <= t <= turn
                }
                subtasks.append(_Subtask(work_start, turn, subtask_ids))
            work_start = None

    return subtasks


def _subtask_summary(calls: dict[str, tuple[int, str, dict]], subtask: _Subtask) -> list[str]:
    """Generate semantic summary lines for a subtask from tool call inputs."""
    lines: list[str] = []
    ordered = sorted(
        ((tid, info) for tid, info in calls.items() if tid in subtask.tool_use_ids),
        key=lambda item: (item[1][0], 0),
    )
    for tid, (turn, name, inp) in ordered:
        if name == "read_file":
            lines.append(f"  read_file: {inp.get('path', '?')}")
        elif name == "glob":
            lines.append(f"  glob: {inp.get('pattern', '?')}")
        elif name == "grep":
            lines.append(f"  grep: {inp.get('pattern', '?')}")
        elif name == "write_file":
            lines.append(f"  write_file: {inp.get('path', '?')}")
        elif name == "patch":
            old_s = str(inp.get("old_str", ""))[:40]
            new_s = str(inp.get("new_str", ""))[:40]
            lines.append(f"  patch: {inp.get('path', '?')} {old_s!r} -> {new_s!r}")
        elif name == "bash":
            cmd = str(inp.get("command", ""))[:80]
            lines.append(f"  bash: {cmd}")
    return lines


def structured_snip(
    messages: list[Message],
    events: list | None = None,
    keep_recent: int = 3,
    tools: list | None = None,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Replace completed read→modify→verify subtasks with a structured summary.

    Zero LLM cost: uses only the structured trajectory events (tool_call /
    tool_result payloads). Closed subtasks older than the most recent
    ``keep_recent`` are collapsed into a single user summary message, keeping
    tool_use/tool_result pairs atomic. When ``events`` is None (backward
    compatible call sites) the layer passes through untouched.
    """
    msgs = deepcopy(messages)
    before = count_tokens(msgs, tools, skip_thinking=skip_thinking)

    if not events:
        return msgs, LayerReport(
            layer="structured_snip",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"skipped": "no_events"},
        )

    normalized = [_norm_event(ev) for ev in events]
    calls: dict[str, tuple[int, str, dict]] = {}
    for ev in normalized:
        tid = _tool_use_id(ev)
        if tid and ev["type"] == "tool_call":
            calls[tid] = (
                ev["turn"] or 0,
                ev["payload"].get("name") or "",
                ev["payload"].get("input") or {},
            )

    subtasks = _detect_subtasks(events)
    if not subtasks:
        return msgs, LayerReport(
            layer="structured_snip",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"skipped": "no_closed_subtasks"},
        )

    compressible = subtasks[:-keep_recent] if keep_recent > 0 else subtasks

    pairs = _find_pairs(msgs)
    affected = 0

    # Compress newest-first so pair indices stay valid while slicing.
    for subtask in reversed(compressible):
        # Collect message-pair indices whose assistant ToolUseBlocks all belong
        # to this subtask (i.e. the whole pair is inside the closed cycle).
        matched: list[tuple[int, int]] = []
        for asst_idx, user_idx in pairs:
            asst_msg = msgs[asst_idx]
            block_ids = {
                b.id for b in asst_msg.content if isinstance(b, ToolUseBlock)
            }
            if block_ids and block_ids <= subtask.tool_use_ids:
                matched.append((asst_idx, user_idx))
        if not matched:
            continue

        summary_lines = _subtask_summary(calls, subtask)
        if not summary_lines:
            continue

        first_a, _ = matched[0]
        _, last_u = matched[-1]
        header = f"[已完成子任务 (turn {subtask.start_turn}-{subtask.end_turn})]"
        summary_text = mark_untrusted(header + "\n" + "\n".join(summary_lines), "压缩摘要")
        summary_block = TextBlock(text=summary_text)
        summary_block.meta["compacted_by"] = "structured_snip"
        summary_block.meta["turn_range"] = [subtask.start_turn, subtask.end_turn]
        msgs = msgs[:first_a] + [Message(role="user", content=[summary_block])] + msgs[last_u + 1:]
        affected += len(matched)
        pairs = _find_pairs(msgs)

    after = count_tokens(msgs, tools, skip_thinking=skip_thinking)
    return msgs, LayerReport(
        layer="structured_snip",
        before_tokens=before,
        after_tokens=after,
        affected=affected,
        skip_thinking=skip_thinking,
        detail={
            "subtasks_detected": len(subtasks),
            "subtasks_compressed": sum(1 for s in compressible),
        },
    )


# ── Layer 3: auto_compact ──────────────────────────────────────────────────

_SUMMARIZE_PROMPT = """你是一个摘要引擎。将以下编码会话压缩为结构化摘要，该摘要将用于继续会话。

严格按以下 Markdown 格式输出（只输出摘要本身，不要其他文字）：

## Goal
用户的原始任务

## Progress
### Done
- 已完成的工作
### In Progress
- 进行中的工作
### Blocked
- 阻塞项或错误

## Key Decisions
- **关键决策**：理由

## Next Steps
1. 下一步要做什么

## Critical Context
关键文件路径、修改内容、错误信息等继续会话所需的数据

<read-files>
本次摘要涉及读取过的文件路径，每行一个
</read-files>

<modified-files>
本次摘要涉及修改过的文件路径，每行一个
</modified-files>

规则：文件清单必须与下方提供的"已读取文件/已修改文件"一致并**累积**（若存在上一次摘要的文件清单，合并进去）；遗漏关键决策会降低后续任务成功率。"""


def _collect_files(to_summarize: list[Message]) -> tuple[list[str], list[str]]:
    """从被压缩消息中提取读取/修改过的文件路径（Pi 风格文件追踪）。"""
    read_files: list[str] = []
    modified_files: list[str] = []
    seen_read: set[str] = set()
    seen_modified: set[str] = set()
    for msg in to_summarize:
        for block in msg.content:
            if not isinstance(block, ToolUseBlock):
                continue
            path = str(block.input.get("path") or "") if isinstance(block.input, dict) else ""
            if not path:
                continue
            if block.name in ("read_file", "read") and path not in seen_read:
                seen_read.add(path)
                read_files.append(path)
            elif block.name in ("write_file", "patch") and path not in seen_modified:
                seen_modified.add(path)
                modified_files.append(path)
    return read_files, modified_files


def auto_compact(
    messages: list[Message],
    backend,
    model: str,
    keep_turns: int = 4,
    tools: list | None = None,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Summarize older turns via LLM, keep system + summary + recent turns."""
    msgs = deepcopy(messages)
    before = count_tokens(msgs, tools, skip_thinking=skip_thinking)

    if len(msgs) < 3:
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"skipped": "too_few_messages"},
        )

    # Keep system + summary + last keep_turns pairs
    system = None
    if len(msgs) > 0 and msgs[0].role == "system":
        system = msgs[0]
    prefix = 1 if system else 0

    pairs = _find_pairs(msgs)
    if len(pairs) <= keep_turns:
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"skipped": "not_enough_pairs_to_summarize"},
        )

    # All pairs before the last keep_turns are eligible for summarization
    summarize_pairs = pairs[:-keep_turns] if keep_turns > 0 else pairs
    assert summarize_pairs, "summarize_pairs should never be empty here"
    keep_start = summarize_pairs[-1][1] + 1

    to_summarize = msgs[prefix:keep_start]

    lines = []
    for msg in to_summarize:
        parts: list[str] = []
        for b in msg.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
            elif isinstance(b, ThinkingBlock):
                truncated = b.text[:200] + "..." if len(b.text) > 200 else b.text
                parts.append(f"[thinking: {truncated}]")
            elif isinstance(b, ToolUseBlock):
                parts.append(f"[tool: {b.name}({b.input})]")
            elif isinstance(b, ToolResultBlock):
                truncated = b.content[:200] + "..." if len(b.content) > 200 else b.content
                parts.append(f"[result: {truncated}]")
        if parts:
            lines.append(f"{msg.role}: {' '.join(parts)}")
    if not lines:
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"skipped": "no_text_to_summarize"},
        )

    request_text = _SUMMARIZE_PROMPT + "\n---\n" + "\n".join(lines)

    # Pi 风格文件追踪：预提取文件清单注入摘要请求（跨轮压缩累积）
    read_files, modified_files = _collect_files(to_summarize)
    file_sections: list[str] = []
    if read_files:
        file_sections.append("已读取文件:\n" + "\n".join(read_files))
    if modified_files:
        file_sections.append("已修改文件:\n" + "\n".join(modified_files))
    if file_sections:
        request_text += "\n\n" + "\n".join(file_sections)

    summary_msg = Message(role="user", content=[TextBlock(text=request_text)])

    try:
        resp = backend.complete(
            messages=[summary_msg],
            tools=None,
            config={"model": model, "stream": False},
        )
    except Exception as e:
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"error": str(e)},
        )

    summary_text = ""
    for block in resp.message.content:
        if isinstance(block, TextBlock):
            summary_text += block.text

    if resp.stop_reason == StopReason.max_tokens:
        summary_text += "\n[摘要已截断]"

    if not summary_text.strip():
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
            detail={"skipped": "empty_summary_from_model"},
        )

    reconstructed: list[Message] = []
    if system:
        reconstructed.append(system)
    reconstructed.append(Message(role="user", content=[TextBlock(text=mark_untrusted(f"[会话摘要]\n{summary_text}", "压缩摘要"))]))
    reconstructed.extend(msgs[keep_start:])

    after = count_tokens(reconstructed, tools, skip_thinking=skip_thinking)
    compacted_history_len = len(to_summarize)

    return reconstructed, LayerReport(
        layer="auto_compact",
        before_tokens=before,
        after_tokens=after,
        affected=compacted_history_len,
        skip_thinking=skip_thinking,
        detail={
            "summary_tokens": resp.usage.output_tokens if resp.usage else 0,
            "summary_input_tokens": resp.usage.input_tokens if resp.usage else 0,
            "original_messages": compacted_history_len,
            "summary_text": summary_text,
        },
    )


# ── Layer 4: truncate ──────────────────────────────────────────────────────

def truncate(
    messages: list[Message],
    budget: int,
    tools: list | None = None,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Keep system + first user + newest messages fitting within budget."""
    msgs = deepcopy(messages)
    before = count_tokens(msgs, tools, skip_thinking)

    if before <= budget:
        return msgs, LayerReport(
            layer="truncate",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
        )

    if len(msgs) < 2:
        return msgs, LayerReport(
            layer="truncate",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            skip_thinking=skip_thinking,
        )

    # Determine must-keep prefix (system + first user task)
    prefix_end = 2 if msgs[0].role == "system" and len(msgs) > 1 and msgs[1].role == "user" else 1

    # Pre-compute per-message tokens (single O(N) pass)
    from vague_code.agent.context_tokens import per_message_tokens
    msg_tokens = per_message_tokens(msgs, skip_thinking)
    tool_tokens = count_tokens([], tools, skip_thinking) if tools else 0
    prefix_sum = sum(msg_tokens[:prefix_end])

    # Guard: if prefix alone exceeds budget, return prefix only (best effort)
    if prefix_sum + tool_tokens >= budget:
        return msgs[:prefix_end], LayerReport(
            layer="truncate",
            before_tokens=before,
            after_tokens=prefix_sum + tool_tokens,
            affected=len(msgs) - prefix_end,
            skip_thinking=skip_thinking,
        )

    # Collect pairs from the tail greedily, keeping tool pairs atomic
    pairs = _find_pairs(msgs)
    relevant_pairs = [(a, u) for a, u in pairs if a >= prefix_end]
    collected: set[int] = set()

    tail_messages: list[Message] = []
    tail_sum = 0
    for asst_idx, user_idx in reversed(relevant_pairs):
        pair_sum = msg_tokens[asst_idx] + msg_tokens[user_idx]
        if prefix_sum + tail_sum + pair_sum + tool_tokens < budget:
            tail_messages = [msgs[asst_idx], msgs[user_idx]] + tail_messages
            tail_sum += pair_sum
            collected.add(asst_idx)
            collected.add(user_idx)
        else:
            break

    # Collect all remaining messages (standalone + gaps) in chronological order.
    # Preserve tool_use/tool_result pair atomicity.
    i = prefix_end
    while i < len(msgs):
        if i in collected:
            i += 1
            continue
        # Check if this assistant has a paired user message right after
        is_pair = (
            msgs[i].role == "assistant"
            and i + 1 < len(msgs)
            and msgs[i + 1].role == "user"
            and (i + 1) not in collected
        )
        if is_pair:
            pair_sum = msg_tokens[i] + msg_tokens[i + 1]
            if prefix_sum + tail_sum + pair_sum + tool_tokens < budget:
                tail_messages.extend([msgs[i], msgs[i + 1]])
                tail_sum += pair_sum
                collected.add(i)
                collected.add(i + 1)
            i += 2
            continue
        if prefix_sum + tail_sum + msg_tokens[i] + tool_tokens < budget:
            tail_messages.append(msgs[i])
            tail_sum += msg_tokens[i]
            collected.add(i)
        i += 1

    dropped = len(msgs) - prefix_end - len(tail_messages)
    reconstructed = msgs[:prefix_end]

    # Determine if a truncation marker can fit
    if dropped > 0:
        for _ in range(len(tail_messages) + 5):
            marker_text = f"[截断: 丢弃 {dropped} 条消息]"
            marker = Message(role="user", content=[TextBlock(text=marker_text)])
            test_m = reconstructed + [marker] + tail_messages
            if count_tokens(test_m, tools, skip_thinking) <= budget:
                reconstructed.append(marker)
                break
            # Drop oldest message(s) from tail to make room
            if not tail_messages:
                break
            if (
                tail_messages[0].role == "assistant"
                and len(tail_messages) > 1
                and tail_messages[1].role == "user"
            ):
                tail_messages = tail_messages[2:]
                dropped += 2
            else:
                tail_messages = tail_messages[1:]
                dropped += 1

    reconstructed.extend(tail_messages)

    after = count_tokens(reconstructed, tools, skip_thinking)
    return reconstructed, LayerReport(
        layer="truncate",
        before_tokens=before,
        after_tokens=after,
        affected=dropped,
        skip_thinking=skip_thinking,
    )


# ── Chain ──────────────────────────────────────────────────────────────────

def compress_chain(
    messages: list[Message],
    tools: list | None,
    cfg,
    budget: int,
    backend=None,
    model: str = "",
    skip_thinking: bool = True,
    events: list | None = None,
) -> tuple[list[Message], list[LayerReport]]:

    if not cfg.enabled:
        return messages, []

    reports: list[LayerReport] = []
    new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

    # 改写闸门（ADR-0035）：利用率 ≤ rewrite_threshold 时完全不动历史（只追加），
    # 保持缓存前缀稳定；超过后一次性执行全部改写型层（stale → micro → structured），
    # 形成一次断裂后缓存重新积累（对齐 Claude Code / Pi 的阈值压缩共识）。
    if new_total > budget * cfg.rewrite_threshold:
        messages, report = stale_snip(messages, cfg.stale_snip_keep_recent, tools, skip_thinking)
        reports.append(report)
        new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

        if new_total > budget * cfg.rewrite_threshold:
            messages, report = microcompact(messages, cfg.microcompact_max_chars, cfg.microcompact_keep_recent, tools, skip_thinking)
            reports.append(report)
            new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

        if events is not None and new_total > budget * cfg.rewrite_threshold:
            messages, report = structured_snip(
                messages, events, cfg.structured_snip_keep_recent, tools, skip_thinking,
            )
            reports.append(report)
            new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

    # Layer 4: auto_compact (util > auto_compact_threshold AND backend available)
    if backend is not None and new_total > budget * cfg.auto_compact_threshold:
        messages, report = auto_compact(messages, backend, model, cfg.auto_compact_keep_turns, tools, skip_thinking)
        reports.append(report)
        new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

    # Layer 5: truncate (still over budget)
    if new_total > budget:
        messages, report = truncate(messages, budget, tools, skip_thinking=skip_thinking)
        reports.append(report)

    return messages, reports

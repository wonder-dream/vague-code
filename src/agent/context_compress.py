from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.context_tokens import count_tokens
from src.agent.ir import (
    Block,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


@dataclass
class LayerReport:
    layer: str
    before_tokens: int
    after_tokens: int
    affected: int
    skip_thinking: bool = True
    detail: dict = field(default_factory=dict)


_READ_TOOLS = frozenset({"read", "glob", "grep"})

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

def _extract_path(tool_block: ToolUseBlock) -> str | None:
    if tool_block.name not in _READ_TOOLS:
        return None
    raw = tool_block.input.get("path") or tool_block.input.get("paths") or tool_block.input.get("pattern")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw:
        return str(raw[0])
    return None


# ── Layer 1: stale_snip ────────────────────────────────────────────────────

def stale_snip(
    messages: list[Message],
    keep_recent: int = 3,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Replace ToolResultBlocks superseded by later same-path reads with a stale placeholder."""
    from copy import deepcopy

    msgs = deepcopy(messages)
    before = count_tokens(msgs, skip_thinking=skip_thinking)

    pairs = _find_pairs(msgs)
    eligible = pairs[:len(pairs) - keep_recent] if keep_recent > 0 else pairs

    # Build path→list of (assistant_idx, user_idx, block_idx, ToolResultBlock)
    path_map: dict[str, list[tuple[int, int, ToolResultBlock]]] = {}

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
                result_map[block.tool_use_id] = block
                block_indices[block.tool_use_id] = bi

        for bi, tool_block in tool_blocks:
            path = _extract_path(tool_block)
            if path is None:
                continue
            result_block = result_map.get(tool_block.id)
            if result_block is None or result_block.is_error:
                continue
            path_map.setdefault(path, []).append((user_idx, block_indices[tool_block.id], result_block))

    affected = 0
    for path, entries in path_map.items():
        if len(entries) <= 1:
            continue
        for _, _, result_block in entries[:-1]:
            result_block.meta["stale"] = True
            result_block.meta["original_stale_content"] = result_block.content
            result_block.content = f"[stale: superseded by later read of {path}]"
            affected += 1

    after = count_tokens(msgs, skip_thinking=skip_thinking)
    return msgs, LayerReport(
        layer="stale_snip",
        skip_thinking=skip_thinking,
        before_tokens=before,
        after_tokens=after,
        affected=affected,
        detail={"paths_stale": sum(1 for v in path_map.values() if len(v) > 1)},
    )


# ── Layer 2: microcompact ──────────────────────────────────────────────────

def _split_lines(content: str):
    return content.splitlines(keepends=True)


def _head_tail(content: str, head_n: int, tail_n: int) -> tuple[str, str, int]:
    lines = _split_lines(content)
    n = len(lines)
    head = "".join(lines[:head_n])
    tail = "".join(lines[-tail_n:]) if tail_n > 0 else ""
    return head, tail, n


def microcompact(
    messages: list[Message],
    max_chars: int = 4000,
    keep_recent: int = 3,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Compact long ToolResultBlock content to head+tail summary."""
    from copy import deepcopy

    msgs = deepcopy(messages)
    before = count_tokens(msgs, skip_thinking=skip_thinking)

    pairs = _find_pairs(msgs)
    eligible = pairs[:len(pairs) - keep_recent] if keep_recent > 0 else pairs
    affected = 0

    for asst_idx, user_idx in eligible:
        msg = msgs[user_idx]
        new_blocks: list[Block] = []
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and not block.is_error:
                if (
                    len(block.content) > max_chars
                    and not block.meta.get("stale")
                    and not block.meta.get("compacted")
                ):
                    head, tail, total_lines = _head_tail(block.content, _HEAD_LINES, _TAIL_LINES)
                    compacted = (
                        f"[compacted: {len(block.content)} chars, {total_lines} lines]"
                        f"\n--- head ({_HEAD_LINES} lines) ---\n{head}"
                        f"\n--- tail ({_TAIL_LINES} lines) ---\n{tail}"
                    )
                    if len(compacted) < len(block.content):
                        block.meta["compacted"] = {
                            "original_chars": len(block.content),
                            "tool_use_id": block.tool_use_id,
                        }
                        block.content = compacted
                        affected += 1
                new_blocks.append(block)
            else:
                new_blocks.append(block)
        msg.content = new_blocks

    after = count_tokens(msgs, skip_thinking=skip_thinking)
    return msgs, LayerReport(
        layer="microcompact",
        skip_thinking=skip_thinking,
        before_tokens=before,
        after_tokens=after,
        affected=affected,
    )


# ── Layer 3: auto_compact ──────────────────────────────────────────────────

_SUMMARIZE_PROMPT = (
    "You are a summarization engine. Summarize the coding session below concisely.\n"
    "Include: the user's original task, what has been done so far, "
    "key file paths and changes made, pending work, and any errors or blockers.\n"
    "The summary will be used to continue the session."
)


def auto_compact(
    messages: list[Message],
    backend,
    model: str,
    keep_turns: int = 4,
    skip_thinking: bool = True,
) -> tuple[list[Message], LayerReport]:
    """Summarize older turns via LLM, keep system + summary + recent turns."""
    from copy import deepcopy

    msgs = deepcopy(messages)
    before = count_tokens(msgs, skip_thinking=skip_thinking)

    if len(msgs) < 3:
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            detail={"skipped": "too_few_messages"},
        )

    # Keep system + summary + last keep_turns pairs
    system = None
    if msgs and msgs[0].role == "system":
        system = msgs[0]
    prefix = 1 if system else 0

    pairs = _find_pairs(msgs)
    if len(pairs) <= keep_turns:
        return msgs, LayerReport(
            layer="auto_compact",
            before_tokens=before,
            after_tokens=before,
            affected=0,
            detail={"skipped": "not_enough_pairs_to_summarize"},
        )

    # All pairs before the last keep_turns are eligible for summarization
    summarize_pairs = pairs[:-keep_turns] if keep_turns > 0 else pairs
    keep_start = summarize_pairs[-1][1] + 1 if summarize_pairs else (prefix if system else 0)

    to_summarize = msgs[prefix:keep_start]

    lines = []
    for msg in to_summarize:
        parts: list[str] = []
        for b in msg.content:
            if isinstance(b, TextBlock):
                parts.append(b.text)
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
            detail={"skipped": "no_text_to_summarize"},
        )

    request_text = _SUMMARIZE_PROMPT + "\n---\n" + "\n".join(lines)
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
            detail={"error": str(e)},
        )

    summary_text = ""
    for block in resp.message.content:
        if isinstance(block, TextBlock):
            summary_text += block.text

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
    reconstructed.append(Message(role="user", content=[TextBlock(text=f"[Session summary]\n{summary_text}")]))
    reconstructed.extend(msgs[keep_start:])

    after = count_tokens(reconstructed, skip_thinking=skip_thinking)
    compacted_history_len = len(to_summarize)

    return reconstructed, LayerReport(
        layer="auto_compact",
        before_tokens=before,
        after_tokens=after,
        affected=compacted_history_len,
        skip_thinking=skip_thinking,
        detail={
            "summary_tokens": resp.usage.output_tokens if resp.usage else 0,
            "original_messages": compacted_history_len,
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
    from copy import deepcopy

    msgs = deepcopy(messages)
    before = count_tokens(msgs, tools, skip_thinking)

    if before <= budget:
        return msgs, LayerReport(
            layer="truncate",
            before_tokens=before,
            after_tokens=before,
            affected=0,
        )

    if len(msgs) < 2:
        return msgs, LayerReport(
            layer="truncate",
            before_tokens=before,
            after_tokens=before,
            affected=0,
        )

    # Determine must-keep prefix (system + first user task)
    prefix_end = 2 if msgs[0].role == "system" and len(msgs) > 1 and msgs[1].role == "user" else 1

    # Collect pairs from the tail greedily, keeping tool pairs atomic
    pairs = _find_pairs(msgs)
    relevant_pairs = [(a, u) for a, u in pairs if a >= prefix_end]

    tail_messages: list[Message] = []
    for asst_idx, user_idx in reversed(relevant_pairs):
        test = msgs[:prefix_end] + tail_messages + [msgs[asst_idx], msgs[user_idx]]
        if count_tokens(test, tools, skip_thinking) < budget:
            tail_messages = [msgs[asst_idx], msgs[user_idx]] + tail_messages
        else:
            break

    last_pair_end = relevant_pairs[-1][1] if relevant_pairs else prefix_end - 1
    for i in range(last_pair_end + 1, len(msgs)):
        test = msgs[:prefix_end] + tail_messages + [msgs[i]]
        if count_tokens(test, tools, skip_thinking) < budget:
            tail_messages.append(msgs[i])

    first_pair_start = relevant_pairs[0][0] if relevant_pairs else len(msgs)
    for i in range(prefix_end, first_pair_start):
        test = msgs[:prefix_end] + tail_messages + [msgs[i]]
        if count_tokens(test, tools, skip_thinking) < budget:
            tail_messages = [msgs[i]] + tail_messages

    dropped = len(msgs) - prefix_end - len(tail_messages)
    reconstructed = msgs[:prefix_end]

    # Determine if a truncation marker can fit
    if dropped > 0:
        for _ in range(len(tail_messages) + 5):
            marker_text = f"[truncated: dropped {dropped} messages to fit token budget]"
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
) -> tuple[list[Message], list[LayerReport]]:

    if not cfg.enabled:
        return messages, []

    reports: list[LayerReport] = []

    # Layer 1: stale_snip (always)
    messages, report = stale_snip(messages, cfg.stale_snip_keep_recent, skip_thinking)
    reports.append(report)

    # Layer 2: microcompact (util > microcompact_threshold)
    new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)
    if new_total > budget * cfg.microcompact_threshold:
        messages, report = microcompact(messages, cfg.microcompact_max_chars, cfg.microcompact_keep_recent, skip_thinking)
        reports.append(report)
        new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

    # Layer 3: auto_compact (util > auto_compact_threshold AND backend available)
    if backend is not None and new_total > budget * cfg.auto_compact_threshold:
        messages, report = auto_compact(messages, backend, model, cfg.auto_compact_keep_turns, skip_thinking)
        reports.append(report)
        new_total = count_tokens(messages, tools, skip_thinking=skip_thinking)

    # Layer 4: truncate (still over budget)
    if new_total > budget:
        messages, report = truncate(messages, budget, tools, skip_thinking=skip_thinking)
        reports.append(report)

    return messages, reports

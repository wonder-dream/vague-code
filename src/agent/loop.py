from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from src.agent.permission import Decision, Operation

from src.agent.backend import ModelBackend
from src.agent.config import AgentConfig
from src.agent.ir import (
    ArgsDelta,
    Block,
    Message,
    MessageEnd,
    MessageStart,
    ModelResponse,
    NormalizedUsage,
    RetryNotice,
    StopReason,
    StreamDisconnect,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ThinkingEnd,
    ThinkingStart,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    ToolUseEnd,
    ToolUseStart,
)
from src.agent.retry import (
    RetryPolicy,
    classify_llm_error,
    estimate_input_tokens,
)
from src.agent.tools import DEFAULT_TOOLS, Tool
from src.agent.trajectory import EventType, Trajectory


# ── Aggregator ──────────────────────────────────────────────────────────────

class _StreamAggregator:
    """Accumulates StreamEvent delta events into a ModelResponse."""

    def __init__(self):
        self._text = StringIO()
        self._thinking = StringIO()
        self._thinking_sig: str | None = None
        self._tool_buffers: dict[str, StringIO] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_order: list[str] = []
        self._result: ModelResponse | None = None

    def feed(self, ev: StreamEvent) -> None:
        if isinstance(ev, TextDelta):
            self._text.write(ev.delta)
        elif isinstance(ev, ThinkingStart):
            pass
        elif isinstance(ev, ThinkingDelta):
            self._thinking.write(ev.delta)
        elif isinstance(ev, ThinkingEnd):
            self._thinking_sig = ev.signature
        elif isinstance(ev, ToolUseStart):
            if ev.id in self._tool_buffers:
                import warnings
                warnings.warn(f"Duplicate ToolUseStart id={ev.id}; resetting buffer")
                self._tool_buffers[ev.id] = StringIO()
            else:
                self._tool_buffers[ev.id] = StringIO()
                self._tool_order.append(ev.id)
            self._tool_names[ev.id] = ev.name
        elif isinstance(ev, ArgsDelta):
            buf = self._tool_buffers.get(ev.id)
            if buf is None:
                raise ValueError(f"ArgsDelta for unknown tool_use_id: {ev.id}")
            buf.write(ev.delta)
        elif isinstance(ev, ToolUseEnd):
            pass
        elif isinstance(ev, MessageEnd):
            # final assembly already done in result()
            pass

    def result(self, message_end: MessageEnd) -> ModelResponse:
        blocks: list[Block] = []
        think_text = self._thinking.getvalue()
        if think_text:
            blocks.append(ThinkingBlock(text=think_text, signature=self._thinking_sig))
        text = self._text.getvalue()
        if text:
            blocks.append(TextBlock(text=text))
        for tid in self._tool_order:
            raw = self._tool_buffers[tid].getvalue()
            name = self._tool_names[tid]
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
            blocks.append(ToolUseBlock(id=tid, name=name, input=parsed))
        if not blocks:
            blocks.append(TextBlock(text=""))
        return ModelResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=message_end.stop_reason,
            usage=message_end.usage or NormalizedUsage(),
        )


# ── RunHandle ──────────────────────────────────────────────────────────────

class RunHandle:
    """Live run handle: iterate for StreamEvents, then read .trajectory."""

    def __init__(self, generator: Iterator[StreamEvent], traj: Trajectory):
        self._generator = generator
        self._traj = traj
        self._finished = False

    def __iter__(self) -> Iterator[StreamEvent]:
        return self

    def __next__(self) -> StreamEvent:
        if self._finished:
            raise StopIteration
        try:
            return next(self._generator)
        except StopIteration:
            self._finished = True
            raise

    def close(self) -> None:
        if not self._finished:
            self._generator.close()  # type: ignore[attr-defined]
            self._finished = True

    def __enter__(self) -> RunHandle:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def trajectory(self) -> Trajectory:
        if not self._finished:
            raise RuntimeError("run not exhausted")
        return self._traj


# ── Agent ──────────────────────────────────────────────────────────────────

class Agent:
    def __init__(
        self,
        config: AgentConfig,
        backend: ModelBackend,
        tools: dict[str, Tool] | None = None,
    ):
        self.config = config
        self.backend = backend
        self._on_permission: Callable[[Operation, Decision], Decision] | None = None
        self.on_tool_result: Callable[[str, str, str, bool], None] | None = None
        self.on_state_change: Callable[[str, dict], None] | None = None
        self.guidance_provider: Callable[[], list[str]] | None = None
        self._permission_rules: list = []
        self._memory_store = None
        self._repo_index: object | None = None
        if config.memory.enabled:
            try:
                from src.agent.memory import MemoryStore
                self._memory_store = MemoryStore(config.memory.memory_db_path)
            except Exception as e:
                import warnings
                warnings.warn(f"Failed to initialize memory store: {e}", stacklevel=2)
        self._tool_registry = tools if tools is not None else DEFAULT_TOOLS
        for key, tool in self._tool_registry.items():
            if key != tool.spec.name:
                raise ValueError(f"Registry key '{key}' does not match tool spec name '{tool.spec.name}'")
        self._tool_specs = [t.spec for t in self._tool_registry.values()]

    def run(self, task: str, workdir: str) -> Trajectory:
        handle = self.start(task, workdir)
        for _ in handle:
            pass
        return handle.trajectory

    def start(self, task: str, workdir: str) -> RunHandle:
        self._workdir = workdir
        run_id = uuid.uuid4().hex[:12]
        traj = Trajectory(run_id=run_id, config=self.config)

        from src.agent.context import SystemPrompt

        # Repo map: build symbol index + optional injection (degradable)
        self._repo_index = None
        repo_map_text = ""
        if self.config.repo_map.enabled:
            try:
                from src.agent.repomap import RepoIndex
                index = RepoIndex(workdir=workdir, max_files=self.config.repo_map.max_files)
                index.build()
                if index.size > 0:
                    self._repo_index = index
                    repo_map_text = index.to_map_text(self.config.repo_map.max_map_tokens)
            except Exception as e:
                import warnings
                warnings.warn(f"Failed to build repo index: {e}", stacklevel=2)
                self._repo_index = None

        system_prompt = SystemPrompt(workdir).build()
        if repo_map_text:
            system_prompt += "\n\n## 代码库符号地图\n" + repo_map_text

        traj.emit(EventType.run_start, payload={
            "task": task,
            "workdir": workdir,
            "system_prompt": system_prompt,
            "config": self.config.to_public_dict(),
            "tools": sorted(self._tool_registry.keys()),
        })

        try:
            bound_tools = {name: t.bind(workdir) for name, t in self._tool_registry.items()}
        except Exception as e:
            traj.emit(EventType.error, payload={"kind": "tool_bind_error", "message": str(e)})
            traj.emit(EventType.run_end, payload={"reason": "tool_bind_error"})
            self._persist(traj)
            return RunHandle(iter([]), traj)

        messages: list[Message] = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=task),
        ]

        # Memory: inject episodic search results when memory_search tool is available
        if self._memory_store and task.strip():
            from src.agent.memory_tool import MEMORY_SEARCH_SPEC, make_memory_search_handler
            self._tool_specs.append(MEMORY_SEARCH_SPEC)
            memory_search_handler = make_memory_search_handler(self._memory_store)
            bound_tools["memory_search"] = memory_search_handler

        # Repo map: register code_search tool when index is available
        if self._repo_index is not None:
            from src.agent.tools import CODE_SEARCH_SPEC, make_code_search_handler
            self._tool_specs.append(CODE_SEARCH_SPEC)
            bound_tools["code_search"] = make_code_search_handler(self._repo_index)

        gen = self._run_gen(traj, messages, [0], bound_tools)
        return RunHandle(gen, traj)

    def _run_gen(
        self,
        traj: Trajectory,
        messages: list[Message],
        turn_box: list[int],
        bound_tools: dict[str, Callable[[dict], str]],
    ) -> Iterator[StreamEvent]:
        try:
            policy = RetryPolicy.from_config(self.config.transport)
            while turn_box[0] < self.config.max_turns:
                turn = turn_box[0]
                guidance = self._drain_guidance()
                if guidance:
                    messages.append(Message(role="user", content="\n".join(guidance)))
                traj.emit(EventType.turn_start, turn=turn)
                self._fire_state_change("turn_start", {"turn": turn})

                call_config = {"model": self.config.model, "stream": self.config.transport.stream}

                retry_index = 0
                resp: ModelResponse | None = None

                from src.agent.context import compress_chain
                from src.agent.context_tokens import compute_budget, count_tokens, should_skip_thinking

                budget = compute_budget(self.config.model)
                cfg = self.config.compression
                skip_thinking = should_skip_thinking(self.config.model)

                try:
                    messages, reports = compress_chain(
                        messages, self._tool_specs, cfg, budget,
                        backend=self.backend, model=self.config.model,
                        skip_thinking=skip_thinking,
                        events=traj.events,
                    )
                except Exception as e:
                    traj.emit(EventType.error, turn=turn, payload={
                        "kind": "compression_error",
                        "message": str(e),
                    })
                    reports = []
                    try:
                        from src.agent.context_compress import truncate
                        messages, _ = truncate(messages, budget, self._tool_specs, skip_thinking)
                    except Exception as _ce:
                        traj.emit(EventType.error, turn=turn, payload={
                            "kind": "truncation_fallback_failed",
                            "message": str(_ce),
                        })
                if reports:
                    for r in reports:
                        traj.emit(EventType.compression, turn=turn, payload={
                            "layer": r.layer,
                            "before_tokens": r.before_tokens,
                            "after_tokens": r.after_tokens,
                            "affected": r.affected,
                            "budget": budget,
                            "skip_thinking": r.skip_thinking,
                            **({"detail": r.detail} if r.detail else {}),
                        })
                else:
                    total = count_tokens(messages, self._tool_specs, skip_thinking=skip_thinking)
                    traj.emit(EventType.compression, turn=turn, payload={
                        "layer": "budget",
                        "before_tokens": total,
                        "after_tokens": total,
                        "affected": 0,
                        "budget": budget,
                        "skip_thinking": skip_thinking,
                        "utilization": round(total / budget, 4) if budget > 0 else 0.0,
                    })
                if reports:
                    r_latest = reports[-1]
                    self._fire_state_change("compression", {
                        "layer": r_latest.layer,
                        "before": r_latest.before_tokens,
                        "after": r_latest.after_tokens,
                        "budget": budget,
                    })
                else:
                    self._fire_state_change("compression", {
                        "layer": "budget", "utilization": 0.0, "budget": budget,
                    })

                # Memory: auto_compact distillation
                if self._memory_store and self.config.memory.auto_compact_distill:
                    for r in reports:
                        if r.layer == "auto_compact" and r.affected > 0 and r.detail.get("summary_text"):
                            self._memory_store.ingest(
                                content=r.detail["summary_text"],
                                kind="episodic",
                                source_session=traj.run_id,
                            )

                while True:
                    aggregator = _StreamAggregator()
                    message_end: MessageEnd | None = None
                    buffered: list[tuple[float, StreamEvent]] = []

                    try:
                        for ev in self._stream_from(messages, self._tool_specs, call_config):
                            buffered.append((time.time(), ev))
                            aggregator.feed(ev)
                            yield ev
                            if isinstance(ev, MessageEnd):
                                message_end = ev
                        if message_end is None:
                            raise StreamDisconnect("Stream ended without MessageEnd")
                        resp = aggregator.result(message_end)
                    except Exception as e:
                        decision = classify_llm_error(e)
                        if not decision.retryable or not policy.enabled or retry_index >= policy.max_attempts:
                            if decision.retryable and policy.enabled:
                                traj.emit(EventType.error, turn=turn, payload={
                                    "kind": "retry_exhausted",
                                    "attempts": retry_index,
                                    "last_error_kind": decision.error_kind,
                                    "message": str(e),
                                })
                            else:
                                traj.emit(EventType.error, turn=turn, payload={"kind": decision.error_kind, "message": str(e)})
                            traj.emit(EventType.run_end, payload={"reason": decision.error_kind})
                            return

                        delay = policy.delay(retry_index)
                        traj.emit(EventType.retry, turn=turn, payload={
                            "attempt": retry_index + 1,
                            "delay_s": delay,
                            "reason": decision.reason,
                            "exception": type(e).__name__,
                            "estimated_input_tokens": estimate_input_tokens(messages, self._tool_specs, skip_thinking=skip_thinking),
                        })
                        yield RetryNotice(attempt=retry_index + 1, delay_s=delay, reason=decision.reason)
                        time.sleep(delay)
                        retry_index += 1
                        continue

                    for ts, ev in buffered:
                        traj.emit(EventType.stream_event, turn=turn, payload=ev.to_dict(), ts=ts)
                    break

                traj.emit(EventType.llm_response, turn=turn, payload={
                    "stop_reason": resp.stop_reason.value,
                    "usage": resp.usage.to_dict(),
                    "blocks": [b.to_dict() for b in resp.message.content],
                })
                self._fire_state_change("llm_response", {
                    "turn": turn, "usage": resp.usage.to_dict(),
                    "stop_reason": resp.stop_reason.value,
                })

                if resp.stop_reason in (StopReason.end_turn, StopReason.stop_sequence):
                    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
                    return

                if resp.stop_reason in (StopReason.max_tokens, StopReason.content_filter, StopReason.unknown):
                    traj.emit(EventType.run_end, payload={"reason": resp.stop_reason.value})
                    return

                if resp.stop_reason == StopReason.tool_use:
                    tool_uses = [b for b in resp.message.content if isinstance(b, ToolUseBlock)]
                    if not tool_uses:
                        traj.emit(EventType.error, turn=turn, payload={"kind": "empty_tool_use", "message": "Model returned tool_use with no ToolUseBlock"})
                        traj.emit(EventType.run_end, payload={"reason": "empty_tool_use"})
                        return
                    if turn + 1 >= self.config.max_turns:
                        traj.emit(EventType.run_end, payload={
                            "reason": "max_turns",
                            "pending_tool_calls": len(tool_uses),
                        })
                        return

                    messages.append(resp.message)
                    self._checkpoint(traj)
                    tool_results: list[Block] = []

                    # Permission check pre-pass (covers ALL tools)
                    from src.agent.permission import Decision, PermissionMode
                    perm_mode = PermissionMode(self.config.permission_mode)
                    allowed_tool_uses: list[ToolUseBlock] = []
                    for block in tool_uses:
                        decision, content, is_error = self._check_tool_permission(
                            block, perm_mode, turn, traj, check_confirm=True,
                        )
                        if decision == Decision.DENY:
                            traj.emit(EventType.tool_call, turn=turn, payload={
                                "id": block.id, "name": block.name, "input": block.input,
                            })
                            traj.emit(EventType.tool_result, turn=turn, payload={
                                "tool_use_id": block.id, "content": content, "is_error": True,
                            })
                            tool_results.append(ToolResultBlock(
                                tool_use_id=block.id, content=content, is_error=True,
                            ))
                            continue
                        allowed_tool_uses.append(block)

                    if not allowed_tool_uses:
                        messages.append(Message(role="user", content=tool_results))
                        turn_box[0] += 1
                        continue

                    if self.config.concurrent_tools and len(allowed_tool_uses) > 1:
                        from src.agent.concurrency import execute_concurrent
                        workdir = getattr(self, "_workdir", "")
                        try:
                            con_results = execute_concurrent(allowed_tool_uses, bound_tools, workdir)
                        except Exception as e:
                            traj.emit(EventType.error, turn=turn, payload={"kind": "concurrent_execution_error", "message": str(e)})
                            traj.emit(EventType.run_end, payload={"reason": "concurrent_execution_error"})
                            return
                        result_by_id = {r.tool_use_id: r for r in con_results}
                        for block in allowed_tool_uses:
                            traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})
                            result = result_by_id.get(block.id)
                            if result is None:
                                content = f"[工具 {block.name} 缺少结果]"
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": True})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content, is_error=True))
                                self._fire_on_tool_result(block.id, block.name, content, True)
                            else:
                                content = self._truncate_tool_content(result.content)
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": result.tool_use_id, "content": content, "is_error": result.is_error})
                                tool_results.append(ToolResultBlock(tool_use_id=result.tool_use_id, content=content, is_error=result.is_error))
                                self._fire_on_tool_result(result.tool_use_id, block.name, content, result.is_error)
                    else:
                        for block in allowed_tool_uses:
                            traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})
                            handler = bound_tools.get(block.name)
                            if handler is None:
                                error_msg = f"未知工具: {block.name}"
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": error_msg, "is_error": True})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))
                                self._fire_on_tool_result(block.id, block.name, error_msg, True)
                                continue
                            try:
                                content = self._truncate_tool_content(handler(block.input))
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": False})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content))
                                self._fire_on_tool_result(block.id, block.name, content, False)
                            except Exception as e:
                                error_msg = f"{type(e).__name__}: {e}"
                                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": error_msg, "is_error": True})
                                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))
                                self._fire_on_tool_result(block.id, block.name, error_msg, True)

                    messages.append(Message(role="user", content=tool_results))
                    turn_box[0] += 1
                    self._checkpoint(traj)

            traj.emit(EventType.run_end, payload={"reason": "max_turns"})
        finally:
            self._persist(traj)

    def _checkpoint(self, traj: Trajectory) -> None:
        try:
            traj.persist()
        except Exception:
            import warnings
            warnings.warn(f"Checkpoint persist failed for run {traj.run_id}", stacklevel=2)

    def _check_tool_permission(
        self,
        block: ToolUseBlock,
        perm_mode,
        turn: int,
        traj: Trajectory,
        *,
        check_confirm: bool = True,
    ) -> tuple:
        """Evaluate permission for a single tool block.

        Returns (decision, content, is_error).
        decision is ALLOW → content="", is_error=False.
        decision is DENY → content is the human-readable error, is_error=True.
        """
        from src.agent.permission import Decision, PermissionMode, Operation, evaluate
        op = Operation(
            tool_name=block.name, input=block.input,
            command=block.input.get("command", "") if block.name == "bash" else "",
        )
        decision = evaluate(perm_mode, op, rules=self._permission_rules)
        traj.emit(EventType.permission_check, turn=turn, payload={
            "tool": block.name, "decision": decision.value,
            "command": (op.command or "")[:200],
        })

        if decision == Decision.DENY:
            content = f"权限不足：当前模式 {perm_mode.value} 禁止此操作"
            if perm_mode == PermissionMode.SAFE:
                content += "\n\n提示：使用 `/mode normal` 切换到更高权限模式。"
            return Decision.DENY, content, True

        if decision == Decision.CONFIRM and check_confirm:
            if self._on_permission:
                from src.agent.prewrite import compute_prewrite_review
                op.review = compute_prewrite_review(
                    block.name, block.input, getattr(self, "_workdir", "")
                )
                decision = self._on_permission(op, decision)
            else:
                decision = Decision.DENY
            if decision == Decision.DENY:
                content = "权限不足"
                if op.feedback:
                    content += f"：{op.feedback}"
                if not self._on_permission:
                    content = "权限不足：无交互确认可用"
                return Decision.DENY, content, True

        return Decision.ALLOW, "", False

    def _drain_guidance(self) -> list[str]:
        if self.guidance_provider is None:
            return []
        try:
            return list(self.guidance_provider())
        except Exception:
            return []

    def add_permission_rule(self, pattern: str, action: str = "allow") -> None:
        from src.agent.permission import Decision, PermissionRule
        self._permission_rules.append(PermissionRule(
            pattern=pattern,
            action=Decision.ALLOW if action == "allow" else Decision.DENY,
        ))

    def _fire_state_change(self, kind: str, payload: dict) -> None:
        if self.on_state_change:
            self.on_state_change(kind, payload)

    def _fire_on_tool_result(self, tool_id: str, tool_name: str, content: str, is_error: bool) -> None:
        if self.on_tool_result:
            self.on_tool_result(tool_id, tool_name, content, is_error)

    @staticmethod
    def _truncate_tool_content(content: str, max_chars: int = 50_000) -> str:
        if len(content) > max_chars:
            return content[:max_chars] + (
                f"\n\n[... 输出截断于 {max_chars} 字符，"
                f"总计: {len(content)} 字符]"
            )
        return content

    TERMINAL_STOP_REASONS = {"end_turn", "stop_sequence", "max_tokens", "content_filter", "unknown"}

    def resume(self, traj: Trajectory) -> Trajectory:
        self._validate_consistent(traj)

        if any(e.type == EventType.run_end for e in traj.events):
            return traj

        workdir = ""
        for e in traj.events:
            if e.type == EventType.run_start:
                workdir = e.payload.get("workdir", "")
                break

        bound_tools = {name: t.bind(workdir) for name, t in self._tool_registry.items()}
        messages = traj.to_messages()

        last_llm = next((e for e in reversed(traj.events) if e.type == EventType.llm_response), None)
        if last_llm is None:
            next_turn = 0
        else:
            sr = last_llm.payload.get("stop_reason")
            T = last_llm.turn or 0
            if sr in self.TERMINAL_STOP_REASONS:
                reason = "end_turn" if sr in ("end_turn", "stop_sequence") else sr
                traj.emit(EventType.run_end, payload={"reason": reason})
                self._persist(traj)
                return traj
            if T + 1 >= self.config.max_turns:
                n = sum(1 for b in last_llm.payload.get("blocks", []) if b.get("type") == "tool_use")
                traj.emit(EventType.run_end, payload={"reason": "max_turns", "pending_tool_calls": n})
                self._persist(traj)
                return traj
            self._execute_pending_tools(traj, messages, T, bound_tools)
            next_turn = T + 1

        gen = self._run_gen(traj, messages, [next_turn], bound_tools)
        for _ in gen:
            pass
        return traj

    def _validate_consistent(self, traj: Trajectory) -> None:
        for e in traj.events:
            if e.type == EventType.run_start:
                saved = e.payload.get("config", {})
                if saved.get("model") and saved["model"] != self.config.model:
                    import warnings
                    warnings.warn(f"Resuming with model {self.config.model}, original was {saved['model']}")
                stored_tools = e.payload.get("tools")
                if stored_tools is not None:
                    current_tools = sorted(self._tool_registry.keys())
                    if not set(stored_tools).issubset(set(current_tools)):
                        raise ValueError(
                            f"Tool registry mismatch on resume: stored={stored_tools}, current={current_tools}"
                        )
                else:
                    import warnings
                    warnings.warn("Resuming run without 'tools' field in run_start — skipping consistency check")
                return

    def _execute_pending_tools(
        self,
        traj: Trajectory,
        messages: list[Message],
        turn: int,
        bound_tools: dict[str, Callable[[dict], str]],
    ) -> bool:
        if not messages or messages[-1].role != "assistant":
            return False
        last_msg = messages[-1]
        pending = [
            b for b in last_msg.content
            if isinstance(b, ToolUseBlock)
            and not any(
                e.type == EventType.tool_result and e.payload.get("tool_use_id") == b.id
                for e in traj.events
            )
        ]
        if not pending:
            return False

        tool_results: list[Block] = []
        for block in pending:
            traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})

            # Permission check (same as _run_gen pre-pass)
            from src.agent.permission import Decision, PermissionMode
            perm_mode = PermissionMode(self.config.permission_mode)
            decision, content, is_error = self._check_tool_permission(
                block, perm_mode, turn, traj, check_confirm=False,
            )
            if decision == Decision.DENY:
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content, is_error=True))
                continue

            handler = bound_tools.get(block.name)
            if handler is None:
                err = f"Unknown tool: {block.name}"
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": err, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=err, is_error=True))
                continue
            try:
                content = self._truncate_tool_content(handler(block.input))
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": False})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content))
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": err, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=err, is_error=True))

        messages.append(Message(role="user", content=tool_results))
        self._checkpoint(traj)
        return True

    def _stream_from(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        config: dict,
    ) -> Iterator[StreamEvent]:
        if config.get("stream") and hasattr(self.backend, "stream"):
            yield from self.backend.stream(messages, tools, config)
        else:
            # Adapter: wrap non-streaming complete() into events
            resp = self.backend.complete(messages, tools, config)
            yield MessageStart(model=config.get("model", "?"))
            for block in resp.message.content:
                if isinstance(block, TextBlock):
                    yield TextDelta(delta=block.text)
                elif isinstance(block, ThinkingBlock):
                    yield ThinkingStart()
                    yield ThinkingDelta(delta=block.text)
                    yield ThinkingEnd(signature=block.signature)
                elif isinstance(block, ToolUseBlock):
                    yield ToolUseStart(id=block.id, name=block.name)
                    yield ArgsDelta(id=block.id, delta=json.dumps(block.input, ensure_ascii=False))
                    yield ToolUseEnd(id=block.id)
            yield MessageEnd(
                stop_reason=resp.stop_reason,
                finish_reason=None,
                truncated=False,
                usage=resp.usage,
            )

    def _persist(self, traj: Trajectory) -> None:
        try:
            traj.persist()
        except Exception:
            import warnings
            warnings.warn("Failed to persist trajectory", stacklevel=2)
            try:
                recovery_path = Path(traj.config.db_path).with_suffix(f".{traj.run_id}.recovery.jsonl")
                recovery_path.parent.mkdir(parents=True, exist_ok=True)
                traj.export_jsonl(recovery_path)
                warnings.warn(f"Trajectory exported to recovery file: {recovery_path}", stacklevel=2)
            except Exception as _re:
                traj.emit(EventType.error, payload={
                    "kind": "persist_recovery_failed",
                    "message": str(_re),
                })
            last = traj.events[-1] if traj.events else None
            if last and last.type != EventType.run_end:
                traj.emit(EventType.run_end, payload={"reason": "persist_failed"})




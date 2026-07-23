from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Callable

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
    response_signature,
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
            if ev.id not in self._tool_buffers:
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
        run_id = uuid.uuid4().hex[:12]
        traj = Trajectory(run_id=run_id, config=self.config)
        traj.emit(EventType.run_start, payload={
            "task": task,
            "workdir": workdir,
            "config": self.config.to_public_dict(),
        })

        try:
            bound_tools = {name: t.bind(workdir) for name, t in self._tool_registry.items()}
        except Exception as e:
            traj.emit(EventType.error, payload={"kind": "tool_bind_error", "message": str(e)})
            traj.emit(EventType.run_end, payload={"reason": "tool_bind_error"})
            self._persist(traj)
            return RunHandle(iter([]), traj)

        messages: list[Message] = [Message(role="user", content=task)]
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
                traj.emit(EventType.turn_start, turn=turn)
                call_config = {"model": self.config.model, "stream": self.config.transport.stream}

                retry_index = 0
                prev_one_fingerprint: dict | None = None
                resp: ModelResponse | None = None

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
                            traj.emit(EventType.run_end, payload={"reason": decision.terminal_reason})
                            return

                        delay = policy.delay(retry_index)
                        traj.emit(EventType.retry, turn=turn, payload={
                            "attempt": retry_index + 1,
                            "delay_s": delay,
                            "reason": decision.reason,
                            "exception": type(e).__name__,
                            "estimated_input_tokens": estimate_input_tokens(messages, self._tool_specs),
                        })
                        yield RetryNotice(attempt=retry_index + 1, delay_s=delay, reason=decision.reason)
                        time.sleep(delay)
                        retry_index += 1
                        continue

                    for ts, ev in buffered:
                        traj.emit(EventType.stream_event, turn=turn, payload=ev.to_dict(), ts=ts)
                    sig = response_signature(resp)
                    if prev_one_fingerprint is not None and sig != prev_one_fingerprint:
                        traj.emit(EventType.retry_divergence, turn=turn, payload={
                            "attempt": retry_index + 1,
                            "previous": prev_one_fingerprint,
                            "current": sig,
                        })
                    break

                traj.emit(EventType.llm_response, turn=turn, payload={
                    "stop_reason": resp.stop_reason.value,
                    "usage": resp.usage.to_dict(),
                    "blocks": [b.to_dict() for b in resp.message.content],
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
                    for block in tool_uses:
                        traj.emit(EventType.tool_call, turn=turn, payload={"id": block.id, "name": block.name, "input": block.input})
                        handler = bound_tools.get(block.name)
                        if handler is None:
                            error_msg = f"Unknown tool: {block.name}"
                            traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": error_msg, "is_error": True})
                            tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))
                            continue
                        try:
                            content = handler(block.input)
                            traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": False})
                            tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content))
                        except Exception as e:
                            error_msg = f"{type(e).__name__}: {e}"
                            traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": error_msg, "is_error": True})
                            tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))

                    messages.append(Message(role="user", content=tool_results))
                    turn_box[0] += 1

            traj.emit(EventType.run_end, payload={"reason": "max_turns"})
        finally:
            self._persist(traj)

    def _checkpoint(self, traj: Trajectory) -> None:
        try:
            traj.persist()
        except Exception:
            import warnings
            warnings.warn(f"Checkpoint persist failed for run {traj.run_id}", stacklevel=2)

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
        turn = self._count_turns(traj)

        had_pending = self._execute_pending_tools(traj, messages, turn, bound_tools)
        if had_pending:
            turn += 1

        gen = self._run_gen(traj, messages, [turn], bound_tools)
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
                return

    def _count_turns(self, traj: Trajectory) -> int:
        turns = set()
        for e in traj.events:
            if e.turn is not None:
                turns.add(e.turn)
        return max(turns) + 1 if turns else 0

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
            handler = bound_tools.get(block.name)
            if handler is None:
                err = f"Unknown tool: {block.name}"
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": err, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=err, is_error=True))
                continue
            try:
                content = handler(block.input)
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": content, "is_error": False})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content))
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                traj.emit(EventType.tool_result, turn=turn, payload={"tool_use_id": block.id, "content": err, "is_error": True})
                tool_results.append(ToolResultBlock(tool_use_id=block.id, content=err, is_error=True))

        messages.append(Message(role="user", content=tool_results))
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
                    yield ArgsDelta(id=block.id, delta=_dump_json(block.input))
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
            except Exception:
                pass
            last = traj.events[-1] if traj.events else None
            if last and last.type != EventType.run_end:
                traj.emit(EventType.run_end, payload={"reason": "persist_failed"})


def _dump_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)

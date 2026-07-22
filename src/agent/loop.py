from __future__ import annotations

import uuid
from pathlib import Path

from openai import APIError, APITimeoutError

from src.agent.backend import ModelBackend
from src.agent.config import AgentConfig
from src.agent.ir import (
    Block,
    Message,
    StopReason,
    ToolResultBlock,
    ToolUseBlock,
)
from src.agent.tools import DEFAULT_TOOLS, Tool
from src.agent.trajectory import EventType, Trajectory


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
            return traj

        messages: list[Message] = [Message(role="user", content=task)]
        turn = 0

        while turn < self.config.max_turns:
            traj.emit(EventType.turn_start, turn=turn)

            try:
                resp = self.backend.complete(
                    messages,
                    tools=self._tool_specs,
                    config={"model": self.config.model},
                )
            except APITimeoutError:
                traj.emit(EventType.error, turn=turn, payload={"kind": "llm_timeout", "message": "LLM call timed out"})
                traj.emit(EventType.run_end, payload={"reason": "llm_timeout"})
                self._persist(traj)
                return traj
            except APIError as e:
                traj.emit(EventType.error, turn=turn, payload={"kind": "llm_error", "message": str(e)})
                traj.emit(EventType.run_end, payload={"reason": "llm_error"})
                self._persist(traj)
                return traj
            except Exception as e:
                traj.emit(EventType.error, turn=turn, payload={"kind": "llm_error", "message": f"{type(e).__name__}: {e}"})
                traj.emit(EventType.run_end, payload={"reason": "llm_error"})
                self._persist(traj)
                return traj

            traj.emit(EventType.llm_response, turn=turn, payload={
                "stop_reason": resp.stop_reason.value,
                "usage": resp.usage.to_dict(),
                "blocks": [b.to_dict() for b in resp.message.content],
            })

            if resp.stop_reason in (StopReason.end_turn, StopReason.stop_sequence):
                traj.emit(EventType.run_end, payload={"reason": "end_turn"})
                break

            if resp.stop_reason in (StopReason.max_tokens, StopReason.content_filter, StopReason.unknown):
                traj.emit(EventType.run_end, payload={"reason": resp.stop_reason.value})
                break

            if resp.stop_reason == StopReason.tool_use:
                tool_uses = [b for b in resp.message.content if isinstance(b, ToolUseBlock)]
                if not tool_uses:
                    traj.emit(EventType.error, turn=turn, payload={"kind": "empty_tool_use", "message": "Model returned tool_use with no ToolUseBlock"})
                    traj.emit(EventType.run_end, payload={"reason": "empty_tool_use"})
                    break
                if turn + 1 >= self.config.max_turns:
                    traj.emit(EventType.run_end, payload={
                        "reason": "max_turns",
                        "pending_tool_calls": len(tool_uses),
                    })
                    break

                messages.append(resp.message)
                tool_results: list[Block] = []
                for block in tool_uses:
                    traj.emit(EventType.tool_call, turn=turn, payload={
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    handler = bound_tools.get(block.name)
                    if handler is None:
                        error_msg = f"Unknown tool: {block.name}"
                        traj.emit(EventType.tool_result, turn=turn, payload={
                            "tool_use_id": block.id,
                            "content": error_msg,
                            "is_error": True,
                        })
                        tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))
                        continue
                    try:
                        content = handler(block.input)
                        traj.emit(EventType.tool_result, turn=turn, payload={
                            "tool_use_id": block.id,
                            "content": content,
                            "is_error": False,
                        })
                        tool_results.append(ToolResultBlock(tool_use_id=block.id, content=content))
                    except Exception as e:
                        error_msg = f"{type(e).__name__}: {e}"
                        traj.emit(EventType.tool_result, turn=turn, payload={
                            "tool_use_id": block.id,
                            "content": error_msg,
                            "is_error": True,
                        })
                        tool_results.append(ToolResultBlock(tool_use_id=block.id, content=error_msg, is_error=True))

                messages.append(Message(role="user", content=tool_results))
                turn += 1

        self._persist(traj)
        return traj

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




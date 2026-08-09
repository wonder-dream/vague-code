"""XClawAgentRunner: bridge between the synchronous Agent and the async Textual UI.

The XClaw Agent runs in a worker thread (`@work(thread=True)`); this runner owns
the agent lifecycle and forwards every event to UI-side callbacks, which the app
installs per turn. Kept free of Textual imports so it can be unit-tested with a
fake agent.

Since ADR-0025 the runner keeps a single Agent instance across turns: chat
messages continue the same session (context continuity), and resume routes by
run mode (chat_resume for chat sessions, resume for task checkpoints).
"""

from __future__ import annotations

from collections.abc import Callable

from src.agent.backend import ModelBackend
from src.agent.config import AgentConfig
from src.agent.ir import StreamEvent
from src.agent.loop import Agent
from src.agent.permission import Decision, Operation


class XClawAgentRunner:
    """Runs `Agent.chat()` / `Agent.resume()` in the caller's thread and forwards events.

    The app must install the callbacks below before calling `run_chat`; all of
    them may be invoked from the agent thread (call_from_thread as needed).
    """

    def __init__(
        self,
        *,
        config: AgentConfig,
        backend: ModelBackend,
        on_stream_event: Callable[[StreamEvent], None] | None = None,
        on_tool_result: Callable[[str, str, str, bool], None] | None = None,
        on_state_change: Callable[[str, dict], None] | None = None,
        on_permission: Callable[[Operation, Decision], Decision] | None = None,
        on_run_complete: Callable[[object], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        permission_rules: list[dict] | None = None,
        guidance_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._config = config
        self._backend = backend
        self.on_stream_event = on_stream_event
        self.on_tool_result = on_tool_result
        self.on_state_change = on_state_change
        self.on_permission = on_permission
        self.on_run_complete = on_run_complete
        self.on_error = on_error
        self._is_cancelled = is_cancelled
        self.permission_rules = permission_rules or []
        self.guidance_provider = guidance_provider
        self._agent: Agent | None = None

    def run_chat(self, task: str, workdir: str) -> None:
        """Run one chat turn (blocking; call from the worker thread)."""
        try:
            handle = self._ensure_agent().chat(task, workdir)
            self._iterate(handle)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(f"{type(exc).__name__}: {exc}")

    def chat_resume(self, run_id: str) -> None:
        """Resume a chat session (blocking; call from the worker thread)."""
        try:
            handle = self._ensure_agent().chat_resume(run_id)
            self._iterate(handle)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(f"{type(exc).__name__}: {exc}")

    def end_chat(self) -> None:
        if self._agent is not None:
            try:
                self._agent.chat_end()
            except Exception:
                pass
            self._agent = None

    def resume(self, traj) -> None:
        """Resume a previous task run (blocking; call from the worker thread)."""
        try:
            agent = self._new_agent()
            self._wire_agent(agent)
            result = agent.resume(traj)
            if self.on_run_complete is not None:
                self.on_run_complete(result)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(f"{type(exc).__name__}: {exc}")

    def _iterate(self, handle) -> None:
        for ev in handle:
            if self._is_cancelled and self._is_cancelled():
                handle.close()
                return
            if self.on_stream_event is not None:
                self.on_stream_event(ev)
        if self.on_run_complete is not None:
            self.on_run_complete(handle.trajectory)

    def _ensure_agent(self) -> Agent:
        if self._agent is None:
            agent = self._new_agent()
            self._wire_agent(agent)
            self._agent = agent
        return self._agent

    def _new_agent(self) -> Agent:
        return Agent(self._config, self._backend)

    def _wire_agent(self, agent: Agent) -> None:
        if self.on_permission is not None:
            agent._on_permission = self.on_permission
        if self.on_tool_result is not None:
            agent.on_tool_result = self.on_tool_result
        if self.on_state_change is not None:
            agent.on_state_change = self.on_state_change
        if self.guidance_provider is not None:
            agent.guidance_provider = self.guidance_provider
        for rule in self.permission_rules:
            agent.add_permission_rule(rule["pattern"], rule.get("action", "allow"))

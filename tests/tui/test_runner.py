from src.agent.permission import Decision, Operation
from src.tui.runner import XClawAgentRunner


class _FakeHandle:
    def __init__(self, events) -> None:
        self._iterator = iter(events)
        self.trajectory = object()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iterator)

    def close(self) -> None:
        self.closed = True


class _FakeAgent:
    def __init__(self, events) -> None:
        self._events = events
        self.rules: list[tuple[str, str]] = []

    def start(self, task: str, workdir: str):
        self.task = task
        self.workdir = workdir
        self.handle = _FakeHandle(self._events)
        return self.handle

    def add_permission_rule(self, pattern: str, action: str = "allow") -> None:
        self.rules.append((pattern, action))


def _make_runner(events, **overrides):
    callbacks = {
        "on_stream_event": [],
        "on_tool_result": [],
        "on_state_change": [],
        "on_run_complete": [],
        "on_error": [],
    }

    def collector(key):
        def handler(*args):
            callbacks[key].append(args)

        return handler

    runner = XClawAgentRunner(
        config=object(),
        backend=object(),
        on_stream_event=collector("on_stream_event"),
        on_tool_result=collector("on_tool_result"),
        on_state_change=collector("on_state_change"),
        on_run_complete=collector("on_run_complete"),
        on_error=collector("on_error"),
        **overrides,
    )
    runner._new_agent = lambda: _FakeAgent(events)
    return runner, callbacks


class _Event:
    def __init__(self, kind: str) -> None:
        self.kind = kind


def test_run_task_forwards_events_and_completion() -> None:
    events = [_Event("message_start"), _Event("text_delta")]
    runner, callbacks = _make_runner(events)
    runner.run_task("hello", "/tmp/work")
    assert len(callbacks["on_stream_event"]) == 2
    assert callbacks["on_run_complete"] and callbacks["on_run_complete"][0][0] is not None
    assert callbacks["on_error"] == []


def test_run_task_cancelled_closes_handle_without_completion() -> None:
    events = [_Event("a"), _Event("b"), _Event("c")]

    def stop_after_first() -> bool:
        return len(callbacks["on_stream_event"]) >= 1

    runner, callbacks = _make_runner(events, is_cancelled=stop_after_first)
    captured: dict = {}

    def new_agent():
        captured["agent"] = _FakeAgent(events)
        return captured["agent"]

    runner._new_agent = new_agent
    runner.run_task("t", ".")
    assert len(callbacks["on_stream_event"]) == 1
    assert callbacks["on_run_complete"] == []
    assert captured["agent"].handle.closed is True


def test_run_task_error_forwarded() -> None:
    def boom():
        raise RuntimeError("backend exploded")

    runner, callbacks = _make_runner([])
    runner._new_agent = boom
    runner.run_task("t", ".")
    assert callbacks["on_error"] and callbacks["on_error"][0][0] == "RuntimeError: backend exploded"


def test_run_task_applies_permission_rules() -> None:
    runner, _ = _make_runner([], permission_rules=[
        {"pattern": "bash echo", "action": "allow"},
    ])
    captured: dict = {}

    def new_agent():
        captured["agent"] = _FakeAgent([])
        return captured["agent"]

    runner._new_agent = new_agent
    runner.run_task("t", ".")
    assert captured["agent"].rules == [("bash echo", "allow")]


def test_permission_callback_wired() -> None:
    calls = []

    def on_permission(op: Operation, decision: Decision) -> Decision:
        calls.append((op, decision))
        return Decision.DENY

    runner, _ = _make_runner([], on_permission=on_permission)
    agent = runner._new_agent()
    runner._wire_agent(agent)
    assert agent._on_permission is on_permission
    result = agent._on_permission(Operation("bash", {"command": "ls"}), Decision.CONFIRM)
    assert result == Decision.DENY
    assert len(calls) == 1

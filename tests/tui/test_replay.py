"""Resume replay tests: rebuild transcript from a persisted trajectory."""

from pathlib import Path

from src.agent.config import AgentConfig
from src.agent.trajectory import EventType, Trajectory
from src.tui.app import XClawApp
from src.tui.state import TuiEntryKind

_TUI_THEME = str(Path(__file__).resolve().parents[2] / "src" / "tui" / "theme.tcss")


class _FakeBackend:
    name = "fake"


class _ReplayApp(XClawApp):
    CSS_PATH = _TUI_THEME


def _build_trajectory(db_path: str, run_id: str = "run1") -> Trajectory:
    config = AgentConfig(model="m", max_turns=2, db_path=db_path)
    traj = Trajectory(run_id=run_id, config=config)
    traj.emit(EventType.run_start, payload={
        "task": "fix the bug",
        "workdir": ".",
        "system_prompt": "sys",
    })
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [
            {"type": "text", "text": "Let me check the file."},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}},
        ],
    })
    traj.emit(EventType.tool_result, turn=0, payload={
        "tool_use_id": "t1", "content": "src/\ntests/", "is_error": False,
    })
    traj.emit(EventType.llm_response, turn=1, payload={
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{"type": "text", "text": "Fixed!"}],
    })
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    return traj


def _make_app(db_path: str) -> _ReplayApp:
    config = AgentConfig(model="m", max_turns=2, db_path=db_path)
    config.permission_mode = "normal"
    return _ReplayApp(config=config, backend=_FakeBackend(), workdir=".")


async def test_replay_trajectory_renders_history() -> None:
    app = _make_app("runs/runs.db")
    traj = _build_trajectory("runs/runs.db")
    async with app.run_test() as pilot:
        await pilot.pause()
        app._replay_trajectory(traj)
        await pilot.pause()

        kinds = [e.kind for e in app.transcript.entries]
        assert kinds[0] == TuiEntryKind.USER
        assert app.transcript.entries[0].body == "fix the bug"
        assistant = [e for e in app.transcript.entries if e.kind == TuiEntryKind.ASSISTANT]
        assert assistant and assistant[0].body == "Let me check the file."
        assert assistant and assistant[1].body == "Fixed!"
        tool = [e for e in app.transcript.entries if e.kind == TuiEntryKind.TOOL]
        assert len(tool) == 1
        assert tool[0].status == "success"
        assert "src/" in tool[0].body


async def test_resume_replays_from_db_then_starts_worker(tmp_path: Path) -> None:
    db_path = str(tmp_path / "runs.db")
    traj = _build_trajectory(db_path)
    traj.persist()

    app = _make_app(db_path)
    app._run_resume_worker = lambda run_id, traj_obj, token: None  # stub the agent
    async with app.run_test() as pilot:
        await pilot.pause()
        app._start_resume("run1")
        await pilot.pause()
        assert any(e.body == "fix the bug" for e in app.transcript.entries)
        assert app._chat_busy is True
        assert app._activity_text.startswith("planning") or "恢复" in [e.body for e in app.transcript.entries]


async def test_resume_missing_run_reports_error(tmp_path: Path) -> None:
    app = _make_app(str(tmp_path / "runs.db"))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._start_resume("missing-run")
        await pilot.pause()
        assert any(e.kind == TuiEntryKind.ERROR for e in app.transcript.entries)

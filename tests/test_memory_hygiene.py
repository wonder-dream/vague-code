"""记忆卫生（ADR-0021）端到端单测：蒸馏冲突检查 + 事实校验 + 事件。

覆盖：
- 普通蒸馏仍走 append
- `[修正: 旧标题]` → replace
- `[作废]` → deprecate
- 校验 block 模式拒绝矛盾记忆并 emit memory_rejected
- 校验 warn 模式标记但不拒绝
"""

from __future__ import annotations

from pathlib import Path

from vague_code.agent.config import AgentConfig, MemoryConfig, MemoryValidationConfig


def test_memory_validation_config_defaults() -> None:
    v = MemoryValidationConfig()
    assert v.enabled is True
    assert v.mode == "warn"


def test_memory_validation_mode_validated() -> None:
    import pytest

    with pytest.raises(ValueError):
        MemoryValidationConfig(mode="bogus")


def test_memory_config_has_validation_field() -> None:
    m = MemoryConfig()
    assert isinstance(m.validation, MemoryValidationConfig)
    assert m.validation.mode == "warn"
from vague_code.agent.ir import (
    Message,
    ModelResponse,
    NormalizedUsage,
    StopReason,
    TextBlock,
)
from vague_code.agent.loop import Agent
from vague_code.agent.trajectory import EventType, Trajectory


class _FakeBackend:
    def __init__(self, text: str):
        self.text = text
        self.call_count = 0
        self.seen_messages: list[Message] = []

    def complete(self, messages, tools=None, config=None):
        self.call_count += 1
        self.seen_messages = list(messages)
        return ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text=self.text)]),
            stop_reason=StopReason.end_turn,
            usage=NormalizedUsage(input_tokens=10, output_tokens=5),
        )


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason=StopReason.end_turn,
        usage=NormalizedUsage(input_tokens=10, output_tokens=5),
    )


def _make_agent(backend, tmp_path, **memory_overrides) -> Agent:
    config = AgentConfig(model="m", max_turns=5, db_path=str(tmp_path / "runs.db"))
    config.compression.enabled = False
    for k, v in memory_overrides.items():
        setattr(config.memory, k, v)
    agent = Agent(config, backend)
    agent._workdir = str(tmp_path)
    return agent


def _traj(agent: Agent, task: str = "任务") -> Trajectory:
    traj = Trajectory(run_id="r" * 12, config=agent.config)
    traj.emit(EventType.run_start, payload={"task": task, "workdir": agent._workdir})
    return traj


def test_distill_normal_append(tmp_path: Path) -> None:
    backend = _FakeBackend("## 构建命令\n用 uv run pytest 跑测试")
    agent = _make_agent(backend, tmp_path)
    agent._distill_session(_traj(agent))
    text = (tmp_path / ".agent" / "memory.md").read_text(encoding="utf-8")
    assert "## 构建命令" in text
    assert "uv run pytest 跑测试" in text


def test_distill_correction_replaces_existing(tmp_path: Path) -> None:
    mf_path = tmp_path / ".agent" / "memory.md"
    mf_path.parent.mkdir(parents=True, exist_ok=True)
    mf_path.write_text(
        "<!-- vague-code memory -->\n\n## 技术栈\n项目用 MySQL\n",
        encoding="utf-8",
    )
    backend = _FakeBackend("## [修正: 技术栈] 技术栈\n项目用 SQLite 做存储")
    agent = _make_agent(backend, tmp_path)
    agent._distill_session(_traj(agent))
    text = mf_path.read_text(encoding="utf-8")
    assert "项目用 SQLite 做存储" in text
    assert "项目用 MySQL" not in text


def test_distill_deprecate_marks_stale(tmp_path: Path) -> None:
    mf_path = tmp_path / ".agent" / "memory.md"
    mf_path.parent.mkdir(parents=True, exist_ok=True)
    mf_path.write_text(
        "<!-- vague-code memory -->\n\n## 技术栈\n项目用 MySQL\n",
        encoding="utf-8",
    )
    backend = _FakeBackend("## [作废] 技术栈\n实际用 SQLite")
    agent = _make_agent(backend, tmp_path)
    agent._distill_session(_traj(agent))
    text = mf_path.read_text(encoding="utf-8")
    assert "stale" in text
    assert "项目用 MySQL" in text  # 保留可见
    assert "实际用 SQLite" in text


def test_distill_validation_block_rejects_contradicted(tmp_path: Path) -> None:
    # workdir 证据：SQLite；记忆声明：MySQL → contradicted
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = [\"sqlite-utils\"]\n", encoding="utf-8",
    )
    (tmp_path / "db.py").write_text("import sqlite3\n", encoding="utf-8")

    backend = _FakeBackend("## 技术栈\n项目用 MySQL 做存储")
    agent = _make_agent(backend, tmp_path)
    agent.config.memory.validation.mode = "block"
    traj = _traj(agent)
    agent._distill_session(traj)

    # 矛盾记忆被拒绝：文件不存在或为空
    mf_path = tmp_path / ".agent" / "memory.md"
    if mf_path.exists():
        assert "MySQL" not in mf_path.read_text(encoding="utf-8")
    # 事件可审计
    assert any(e.type == EventType.memory_rejected for e in traj.events)


def test_distill_validation_warn_marks_but_appends(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = [\"sqlite-utils\"]\n", encoding="utf-8",
    )
    (tmp_path / "db.py").write_text("import sqlite3\n", encoding="utf-8")

    backend = _FakeBackend("## 技术栈\n项目用 MySQL 做存储")
    agent = _make_agent(backend, tmp_path)
    # 默认 warn
    agent._distill_session(_traj(agent))
    text = (tmp_path / ".agent" / "memory.md").read_text(encoding="utf-8")
    assert "项目用 MySQL" in text
    assert "矛盾" in text or "unverified" in text


def test_distill_validation_off_is_noop(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = [\"sqlite-utils\"]\n", encoding="utf-8",
    )
    (tmp_path / "db.py").write_text("import sqlite3\n", encoding="utf-8")

    backend = _FakeBackend("## 技术栈\n项目用 MySQL 做存储")
    agent = _make_agent(backend, tmp_path)
    agent.config.memory.validation.mode = "off"
    agent._distill_session(_traj(agent))
    text = (tmp_path / ".agent" / "memory.md").read_text(encoding="utf-8")
    assert "项目用 MySQL" in text
    assert "矛盾" not in text and "unverified" not in text

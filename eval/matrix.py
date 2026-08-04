from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalCell:
    compression: bool
    concurrency: bool
    repo_map: bool
    repeat: int


@dataclass
class TaskResult:
    instance_id: str
    cell: EvalCell
    passed: bool | None
    error: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    trajectory_path: str = ""
    run_id: str = ""
    verified: bool | None = None
    f2p_pass: bool | None = None
    p2p_pass: bool | None = None
    verdict_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "cell": cell_label(self.cell),
            "passed": self.passed,
            "error": self.error,
            "stats": self.stats,
            "trajectory_path": self.trajectory_path,
            "run_id": self.run_id,
            "verified": self.verified,
            "f2p_pass": self.f2p_pass,
            "p2p_pass": self.p2p_pass,
            "verdict_reason": self.verdict_reason,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TaskResult":
        return TaskResult(
            instance_id=d["instance_id"],
            cell=parse_cell_label(d["cell"]),
            passed=d.get("passed"),
            error=d.get("error"),
            stats=d.get("stats") or {},
            trajectory_path=d.get("trajectory_path", ""),
            run_id=d.get("run_id", ""),
            verified=d.get("verified"),
            f2p_pass=d.get("f2p_pass"),
            p2p_pass=d.get("p2p_pass"),
            verdict_reason=d.get("verdict_reason", ""),
        )


def build_matrix(
    repeat: int = 3,
    design: str = "ofat",
    ablation_repeat: int | None = None,
) -> list[EvalCell]:
    """消融矩阵。

    - design="ofat"（默认，One-Factor-At-a-Time）：基线全开 + 3 个单变量关闭，
      共 4 配置。测不了交互效应（报告声明），但把全因子 8 配置腰斩一半。
    - design="full"：2×2×2 全因子 × repeat。
    全开配置重复 repeat 次（pass^k 主数字）；消融配置重复 ablation_repeat 次
    （默认同 repeat，分层任务集时可用 k=2 控成本）。
    """
    def _add(compression: bool, concurrency: bool, repo_map: bool, reps: int) -> None:
        for rep in range(reps):
            cells.append(EvalCell(compression, concurrency, repo_map, rep))

    cells: list[EvalCell] = []
    ab = ablation_repeat if ablation_repeat is not None else repeat
    if design == "full":
        for compression in [True, False]:
            for concurrency in [True, False]:
                for repo_map in [True, False]:
                    _add(compression, concurrency, repo_map, repeat)
    else:
        _add(True, True, True, repeat)        # 基线全开（k=repeat）
        _add(False, True, True, ab)           # 只关压缩
        _add(True, False, True, ab)           # 只关并发
        _add(True, True, False, ab)           # 只关 RepoMap
    return cells


def cell_label(cell: EvalCell) -> str:
    parts = []
    parts.append("C" if cell.compression else "nc")
    parts.append("X" if cell.concurrency else "sx")
    parts.append("M" if cell.repo_map else "nm")
    parts.append(f"r{cell.repeat}")
    return "_".join(parts)


def parse_cell_label(label: str) -> EvalCell:
    """cell_label 的逆：'C_X_M_r0' → EvalCell。容错未知字段（旧版存档）。"""
    parts = label.split("_")
    def flag(key: str) -> bool:
        return key in parts
    return EvalCell(
        compression=flag("C"),
        concurrency=flag("X"),
        repo_map=flag("M"),
        repeat=next((int(p[1:]) for p in parts if p.startswith("r") and p[1:].isdigit()), 0),
    )

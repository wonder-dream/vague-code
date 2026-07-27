from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalCell:
    compression: bool
    concurrency: bool
    repeat: int


@dataclass
class TaskResult:
    instance_id: str
    cell: EvalCell
    passed: bool | None
    error: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    trajectory_path: str = ""


def build_matrix(repeat: int = 3) -> list[EvalCell]:
    cells = []
    for compression in [True, False]:
        for concurrency in [True, False]:
            for rep in range(repeat):
                cells.append(EvalCell(
                    compression=compression,
                    concurrency=concurrency,
                    repeat=rep,
                ))
    return cells  # 2×2×repeat


def cell_label(cell: EvalCell) -> str:
    parts = []
    parts.append("C" if cell.compression else "nc")
    parts.append("X" if cell.concurrency else "sx")
    parts.append(f"r{cell.repeat}")
    return "_".join(parts)

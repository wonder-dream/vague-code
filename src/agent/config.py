from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


@dataclass
class AgentConfig:
    model: str = "deepseek-v4-flash"
    max_turns: int = 20
    turn_timeout_s: float = 120.0
    db_path: str = "runs/runs.db"

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError(f"max_turns must be >= 1, got {self.max_turns}")
        if self.max_turns > 500:
            import warnings
            warnings.warn(f"max_turns={self.max_turns} is unusually high, consider a lower value")
        if self.turn_timeout_s <= 0:
            raise ValueError(f"turn_timeout_s must be > 0, got {self.turn_timeout_s}")
        stripped = self.model.strip()
        if not stripped:
            raise ValueError("model must not be empty")
        if not MODEL_ID_RE.match(stripped):
            raise ValueError(f"model contains invalid characters: {self.model!r}")
        if not self.db_path.strip():
            raise ValueError("db_path must not be empty")
        if Path(self.db_path).suffix != ".db":
            raise ValueError(f"db_path must end with .db, got {self.db_path!r}")

    def to_public_dict(self) -> dict:
        return asdict(self)

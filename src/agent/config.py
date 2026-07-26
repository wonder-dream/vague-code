from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


@dataclass
class TransportConfig:
    stream: bool = True
    timeout_s: float = 120.0
    retry_enabled: bool = True
    retry_max_attempts: int = 5
    retry_base_s: float = 2.0
    retry_max_delay_s: float = 120.0

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be > 0, got {self.timeout_s}")
        if self.retry_max_attempts < 0:
            raise ValueError(f"retry_max_attempts must be >= 0, got {self.retry_max_attempts}")
        if self.retry_base_s <= 0:
            raise ValueError(f"retry_base_s must be > 0, got {self.retry_base_s}")
        if self.retry_max_delay_s <= 0:
            raise ValueError(f"retry_max_delay_s must be > 0, got {self.retry_max_delay_s}")

@dataclass
class AgentConfig:
    model: str = "deepseek-v4-flash"
    max_turns: int = 20
    db_path: str = "runs/runs.db"
    transport: TransportConfig = field(default_factory=TransportConfig)

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError(f"max_turns must be >= 1, got {self.max_turns}")
        if self.max_turns > 500:
            import warnings
            warnings.warn(f"max_turns={self.max_turns} is unusually high, consider a lower value")
        stripped = self.model.strip()
        if not stripped:
            raise ValueError("model must not be empty")
        if not MODEL_ID_RE.match(stripped):
            raise ValueError(f"model contains invalid characters: {self.model!r}")
        if not self.db_path.strip():
            raise ValueError("db_path must not be empty")
        if not self.db_path.endswith((".db", ".sqlite")):
            raise ValueError(f"db_path must end with .db, got {self.db_path!r}")

    def to_public_dict(self) -> dict:
        return asdict(self)

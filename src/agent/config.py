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
class CompressionConfig:
    enabled: bool = True
    microcompact_threshold: float = 0.5
    microcompact_max_chars: int = 4000
    microcompact_keep_recent: int = 3
    auto_compact_threshold: float = 0.85
    auto_compact_keep_turns: int = 4
    stale_snip_keep_recent: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.microcompact_threshold <= 1.0:
            raise ValueError(f"microcompact_threshold must be in [0,1], got {self.microcompact_threshold}")
        if not 0.0 <= self.auto_compact_threshold <= 1.0:
            raise ValueError(f"auto_compact_threshold must be in [0,1], got {self.auto_compact_threshold}")
        if self.microcompact_max_chars < 1:
            raise ValueError(f"microcompact_max_chars must be >= 1, got {self.microcompact_max_chars}")
        if self.microcompact_keep_recent < 0:
            raise ValueError(f"microcompact_keep_recent must be >= 0, got {self.microcompact_keep_recent}")
        if self.stale_snip_keep_recent < 0:
            raise ValueError(f"stale_snip_keep_recent must be >= 0, got {self.stale_snip_keep_recent}")
        if self.auto_compact_keep_turns < 0:
            raise ValueError(f"auto_compact_keep_turns must be >= 0, got {self.auto_compact_keep_turns}")


@dataclass
class AgentConfig:
    model: str = "deepseek-v4-flash"
    max_turns: int = 20
    db_path: str = "runs/runs.db"
    transport: TransportConfig = field(default_factory=TransportConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    concurrent_tools: bool = False

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

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9._\-/]+$")  # / 支持 OpenRouter 的 provider/model 模型名


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
    # 改写闸门（ADR-0035）：利用率 ≤ rewrite_threshold 时完全不动历史（只追加），
    # 保持缓存前缀稳定；超过后一次性执行全部改写型层（stale→micro→structured）。
    rewrite_threshold: float = 0.7
    microcompact_max_chars: int = 4000
    microcompact_keep_recent: int = 3
    structured_snip_keep_recent: int = 3
    auto_compact_threshold: float = 0.85
    auto_compact_keep_turns: int = 4
    stale_snip_keep_recent: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.rewrite_threshold <= 1.0:
            raise ValueError(f"rewrite_threshold must be in [0,1], got {self.rewrite_threshold}")
        if not 0.0 <= self.auto_compact_threshold <= 1.0:
            raise ValueError(f"auto_compact_threshold must be in [0,1], got {self.auto_compact_threshold}")
        if self.microcompact_max_chars < 1:
            raise ValueError(f"microcompact_max_chars must be >= 1, got {self.microcompact_max_chars}")
        if self.microcompact_keep_recent < 0:
            raise ValueError(f"microcompact_keep_recent must be >= 0, got {self.microcompact_keep_recent}")
        if self.structured_snip_keep_recent < 0:
            raise ValueError(f"structured_snip_keep_recent must be >= 0, got {self.structured_snip_keep_recent}")
        if self.stale_snip_keep_recent < 0:
            raise ValueError(f"stale_snip_keep_recent must be >= 0, got {self.stale_snip_keep_recent}")
        if self.auto_compact_keep_turns < 0:
            raise ValueError(f"auto_compact_keep_turns must be >= 0, got {self.auto_compact_keep_turns}")


@dataclass
class MemoryConfig:
    enabled: bool = True
    memory_db_path: str = "runs/memory.db"
    auto_compact_distill: bool = True


@dataclass
class RepoMapConfig:
    enabled: bool = True
    max_map_tokens: int = 1000
    max_files: int = 2000


@dataclass
class SupervisionConfig:
    """Supervision Agent 配置（ADR-0020）：监督者无工具，只"看"与"说"。

    enabled=False 为默认（ADR-0020 #8：未充分评测前不作为产品默认路径）。
    period: 周期监督的轮数间隔；model: None = 同主 agent 模型。
    """
    enabled: bool = False
    period: int = 6
    model: str | None = None
    max_input_tokens: int = 6000
    stuck_limit: int = 2

    def __post_init__(self) -> None:
        if self.period < 1:
            raise ValueError(f"period must be >= 1, got {self.period}")
        if self.stuck_limit < 1:
            raise ValueError(f"stuck_limit must be >= 1, got {self.stuck_limit}")
        if self.max_input_tokens < 1:
            raise ValueError(f"max_input_tokens must be >= 1, got {self.max_input_tokens}")


@dataclass
class AgentConfig:
    model: str = "deepseek-v4-flash"
    max_turns: int = 500
    db_path: str = "runs/runs.db"
    transport: TransportConfig = field(default_factory=TransportConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    concurrent_tools: bool = False
    permission_mode: str = "normal"
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    repo_map: RepoMapConfig = field(default_factory=RepoMapConfig)
    supervision: SupervisionConfig = field(default_factory=SupervisionConfig)
    max_output_tokens: int = 32768  # 每轮输出预算（含 thinking）；评测可调大（ADR-0040）
    reasoning_effort: str | None = None  # "low"/"high"（deepseek/openai）；None=模型默认

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError(f"max_turns must be >= 1, got {self.max_turns}")
        if self.max_turns > 500:
            import warnings
            warnings.warn(f"max_turns={self.max_turns} is unusually high, consider a lower value")
        if self.max_output_tokens < 1024:
            raise ValueError(f"max_output_tokens must be >= 1024, got {self.max_output_tokens}")
        if self.reasoning_effort not in (None, "low", "high"):
            raise ValueError(
                f"reasoning_effort must be 'low'/'high'/None, got {self.reasoning_effort!r}")
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

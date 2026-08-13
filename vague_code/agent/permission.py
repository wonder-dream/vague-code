from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Decision(Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class PermissionMode(Enum):
    SAFE = "safe"
    NORMAL = "normal"
    AUTOEDIT = "autoedit"
    AUTO = "auto"


class DangerLevel(Enum):
    SAFE = "safe"
    DANGEROUS = "dangerous"


@dataclass
class Operation:
    tool_name: str
    input: dict
    command: str | None = None
    review: dict | None = None
    feedback: str | None = None


@dataclass
class PermissionRule:
    pattern: str
    action: Decision
    scope: str = "global"


# ── Dangerous command classification ────────────────────────────────────────

_SAFE_COMMANDS: tuple[str, ...] = (
    r"^\s*ls\b",
    r"^\s*git\s+(status|log|diff|branch|show|blame|grep)\b",
    r"^\s*cat\b",
    r"^\s*head\b",
    r"^\s*tail\b",
    r"^\s*wc\b",
    r"^\s*echo\b",
    r"^\s*pwd\b",
    r"^\s*which\b",
    r"^\s*whoami\b",
    r"^\s*id\b",
    r"^\s*uname\b",
    r"^\s*date\b",
    r"^\s*type\b",
    r"^\s*cp\b",
    r"^\s*mv\b",
)

_DANGEROUS_COMMANDS: tuple[str, ...] = (
    r"^\s*rm\b",
    r"^\s*rmdir\b",
    r"^\s*dd\b",
    r"^\s*chmod\b",
    r"^\s*chown\b",
    r"^\s*ln\b",
    r"^\s*kill\b",
    r"^\s*killall\b",
    r"^\s*pkill\b",
    r"^\s*reboot\b",
    r"^\s*shutdown\b",
    r"curl\s+\S+\s*\|\s*(sh|bash|zsh)",
    r"wget\s+\S+\s*\|\s*(sh|bash|zsh)",
    r"^\s*python\s+-c\b",
    r"^\s*bash\s+-c\b",
    r"sed\s+-i\b",
    r"find\s+.*-delete\b",
    r"fuser\b",
    r"mkfs\b",
    r"fdisk\b",
    r"^\s*exec\b",
    r"eval\b",
    r">\s*/dev/(null|zero|random|urandom)",
    # ── 2026-08-10 补盲（M5）：git 破坏性操作 / 包安装 / 进程杀死 ──
    r"git\s+reset\s+--hard\b",
    r"git\s+clean\b",
    r"git\s+checkout\s+--\b",
    r"git\s+restore\b",
    r"pip3?\s+install\b",
    r"npm\s+(install|i)\b",
    r"yarn\s+add\b",
    r"taskkill\b",
    r"format\s+[a-zA-Z]:",
)

_SAFE_PATTERNS = [re.compile(p) for p in _SAFE_COMMANDS]
_DANGEROUS_PATTERNS = [re.compile(p) for p in _DANGEROUS_COMMANDS]


def classify_bash(command: str) -> DangerLevel:
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return DangerLevel.DANGEROUS
    for pattern in _SAFE_PATTERNS:
        if pattern.search(command):
            return DangerLevel.SAFE
    return DangerLevel.DANGEROUS


_DEFAULT_POLICIES: dict[PermissionMode, dict[str, Decision]] = {
    PermissionMode.SAFE: {
        "read": Decision.ALLOW,
        "write": Decision.DENY,
        "bash_safe": Decision.DENY,
        "bash_dangerous": Decision.DENY,
        "network": Decision.DENY,
    },
    PermissionMode.NORMAL: {
        "read": Decision.ALLOW,
        "write": Decision.CONFIRM,
        "bash_safe": Decision.CONFIRM,
        "bash_dangerous": Decision.CONFIRM,
        "network": Decision.CONFIRM,
    },
    PermissionMode.AUTOEDIT: {
        "read": Decision.ALLOW,
        "write": Decision.ALLOW,
        "bash_safe": Decision.CONFIRM,
        "bash_dangerous": Decision.CONFIRM,
        "network": Decision.CONFIRM,
    },
    PermissionMode.AUTO: {
        "read": Decision.ALLOW,
        "write": Decision.ALLOW,
        "bash_safe": Decision.ALLOW,
        "bash_dangerous": Decision.CONFIRM,
        "network": Decision.ALLOW,
    },
}


def evaluate(
    mode: PermissionMode,
    permission_class: str,
    operation: Operation | None = None,
    rules: list[PermissionRule] | None = None,
) -> Decision:
    """按权限分类评估（ADR-0004 重构：分类由工具元数据提供，替代按工具名分支）。

    permission_class：read / write / bash_safe / bash_dangerous / network
    （来自 Tool.permission_class()；未知工具回退 "write"，与旧行为一致）。
    operation 仅用于持久化规则匹配（tool_name + input repr）。
    """
    if rules and operation is not None:
        op_repr = operation.tool_name + " " + str(operation.input)
        for rule in rules:
            if not rule.pattern:
                continue
            if _rule_matches(rule.pattern, op_repr):
                return rule.action

    policy = _DEFAULT_POLICIES.get(mode, _DEFAULT_POLICIES[PermissionMode.NORMAL])
    if permission_class not in policy:
        permission_class = "write"
    return policy[permission_class]


def _rule_matches(pattern: str, op_repr: str) -> bool:
    """Match a persisted rule against an operation repr.

    Rules are persisted from raw tool text (e.g. `bash {'command': 'for /R
    src %f in (*.py) ...'}`) which may not be a valid regex. Fall back to
    literal (escaped) matching so one bad rule never breaks permission
    evaluation.
    """
    try:
        return re.search(pattern, op_repr) is not None
    except re.error:
        try:
            return re.search(re.escape(pattern), op_repr) is not None
        except re.error:
            return False

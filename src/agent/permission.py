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
    r"^\s*env\b",
    r"^\s*date\b",
    r"^\s*printenv\b",
    r"^\s*type\b",
)

_DANGEROUS_COMMANDS: tuple[str, ...] = (
    r"^\s*rm\b",
    r"^\s*rmdir\b",
    r"^\s*dd\b",
    r"^\s*chmod\b",
    r"^\s*chown\b",
    r"^\s*mv\b",
    r"^\s*cp\b",
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
    operation: Operation,
    rules: list[PermissionRule] | None = None,
) -> Decision:
    if rules:
        op_repr = operation.tool_name + " " + str(operation.input)
        for rule in rules:
            if re.search(rule.pattern, op_repr):
                return rule.action

    policy = _DEFAULT_POLICIES.get(mode, _DEFAULT_POLICIES[PermissionMode.NORMAL])

    if operation.tool_name == "bash":
        level = classify_bash(operation.command or "")
        return policy["bash_safe" if level == DangerLevel.SAFE else "bash_dangerous"]

    if operation.tool_name in ("read_file", "glob", "grep"):
        return policy["read"]

    if operation.tool_name in ("write_file", "patch"):
        return policy["write"]

    return policy["write"]

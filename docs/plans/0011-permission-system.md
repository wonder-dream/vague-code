# 0011: 权限系统

## 原则

- 纯函数决策，可脱离 Agent 单测
- 默认安全（normal 模式 + deny 默认）
- 危险命令用正则静态分类
- 可审计（每次决策轨迹事件）

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `src/agent/permission.py` | **新建** |
| 2 | `src/agent/config.py` | 改：`AgentConfig` 加权限字段 |
| 3 | `src/agent/loop.py` | 改：bash 执行前接入权限 |
| 4 | `src/agent/trajectory.py` | 改：`EventType` 加权限事件 |
| 5 | `tests/test_permission.py` | **新建** |

## 步骤 1：`permission.py`

### 1.1 枚举与数据类

```python
from __future__ import annotations
from enum import Enum
from dataclasses import dataclass


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
    command: str | None = None  # bash 时可用


@dataclass
class PermissionRule:
    pattern: str
    action: Decision
    scope: str = "global"  # global / session / once
```

### 1.2 危险命令正则

```python
_SAFE_COMMANDS: tuple[str, ...] = (
    r"^\s*ls\b", r"^\s*git\s+(status|log|diff|branch|show|blame|grep)\b",
    r"^\s*cat\b", r"^\s*head\b", r"^\s*tail\b", r"^\s*wc\b",
    r"^\s*echo\b", r"^\s*pwd\b", r"^\s*which\b", r"^\s*whoami\b",
    r"^\s*id\b", r"^\s*uname\b", r"^\s*env\b", r"^\s*date\b",
    r"^\s*printenv\b", r"^\s*type\b",
)

_DANGEROUS_COMMANDS: tuple[str, ...] = (
    r"^\s*rm\b", r"^\s*rmdir\b",
    r"^\s*dd\b",
    r"^\s*chmod\b", r"^\s*chown\b",
    r"^\s*mv\b", r"^\s*cp\b", r"^\s*ln\b",
    r"^\s*kill\b", r"^\s*killall\b", r"^\s*pkill\b",
    r"^\s*reboot\b", r"^\s*shutdown\b",
    r"curl\s+\S+\s*\|\s*(sh|bash|zsh)", r"wget\s+\S+\s*\|\s*(sh|bash|zsh)",
    r"^\s*python\s+-c\b", r"^\s*bash\s+-c\b",
    r"sed\s+-i\b",
    r"find\s+.*-delete\b",
    r"fuser\b", r"mkfs\b", r"fdisk\b",
    r"^\s*exec\b", r"eval\b",
    r">\s*/dev/(null|zero|random|urandom)", r"^\s*dd\b",
)
```

### 1.3 命令行分类

```python
import re

def classify_bash(command: str) -> DangerLevel:
    for pattern in _DANGEROUS_COMMANDS:
        if re.search(pattern, command):
            return DangerLevel.DANGEROUS
    for pattern in _SAFE_COMMANDS:
        if re.search(pattern, command):
            return DangerLevel.SAFE
    return DangerLevel.DANGEROUS  # 未知命令保守为危险
```

### 1.4 默认策略表

```python
# (mode, read, write, bash_safe, bash_dangerous, network)
_DEFAULT_POLICIES: dict[PermissionMode, dict[str, Decision]] = {
    PermissionMode.SAFE:     {"read": Decision.ALLOW,  "write": Decision.DENY,
                              "bash_safe": Decision.DENY, "bash_dangerous": Decision.DENY,
                              "network": Decision.DENY},
    PermissionMode.NORMAL:   {"read": Decision.ALLOW,  "write": Decision.CONFIRM,
                              "bash_safe": Decision.CONFIRM, "bash_dangerous": Decision.CONFIRM,
                              "network": Decision.CONFIRM},
    PermissionMode.AUTOEDIT: {"read": Decision.ALLOW,  "write": Decision.ALLOW,
                              "bash_safe": Decision.CONFIRM, "bash_dangerous": Decision.CONFIRM,
                              "network": Decision.CONFIRM},
    PermissionMode.AUTO:     {"read": Decision.ALLOW,  "write": Decision.ALLOW,
                              "bash_safe": Decision.ALLOW, "bash_dangerous": Decision.CONFIRM,
                              "network": Decision.ALLOW},
}
```

### 1.5 决策函数

```python
def evaluate(
    mode: PermissionMode,
    operation: Operation,
    rules: list[PermissionRule] | None = None,
) -> Decision:
    # 规则优先匹配
    if rules:
        for rule in rules:
            if re.search(rule.pattern, operation.tool_name + " " + str(operation.input)):
                return rule.action
    # 模式默认策略
    policy = _DEFAULT_POLICIES.get(mode, _DEFAULT_POLICIES[PermissionMode.NORMAL])
    if operation.tool_name == "bash":
        if classify_bash(operation.command or "") == DangerLevel.SAFE:
            return policy["bash_safe"]
        return policy["bash_dangerous"]
    if operation.tool_name in ("read_file", "glob", "grep"):
        return policy["read"]
    if operation.tool_name in ("write_file", "patch"):
        return policy["write"]
    return policy["write"]  # 未知工具保守走 write 策略
```

## 步骤 2：`config.py`

```python
# AgentConfig 新增
permission_mode: str = "normal"  # safe / normal / autoedit / auto
```

## 步骤 3：`loop.py`

在 bash handler 前插入权限检查：

```python
if block.name == "bash":
    from src.agent.permission import evaluate, Operation, PermissionMode
    mode = PermissionMode(self.config.permission_mode)
    op = Operation(tool_name=block.name, input=block.input, command=block.input.get("command", ""))
    decision = evaluate(mode, op)
    traj.emit(EventType.permission_check, turn=turn, payload={
        "tool": block.name, "decision": decision.value,
        "command": block.input.get("command", "")[:200],
    })
    if decision == Decision.DENY:
        content = f"Permission denied: mode {mode.value} blocks this operation"
        if block.name == "bash" and mode == PermissionMode.SAFE:
            content += "\n\nTip: Switch to normal mode with `/mode normal` to allow shell commands."
        traj.emit(...tool_result=error...)
        continue
    elif decision == Decision.CONFIRM:
        if self._on_permission is not None:
            decision = self._on_permission(op, decision)
        if decision == Decision.DENY:
            content = f"Permission denied by user"
            traj.emit(...tool_result=error...)
            continue
```

## 步骤 4：`trajectory.py`

```python
class EventType(str, Enum):
    ...
    permission_check = "permission_check"
    mode_change = "mode_change"
```

## 测试

| 测试 | 验证点 |
|------|--------|
| `test_classify_bash_safe` | `ls -la` → SAFE |
| `test_classify_bash_dangerous` | `rm -rf /` → DANGEROUS |
| `test_classify_bash_unknown` | `some_random_tool arg` → DANGEROUS |
| `test_evaluate_safe_bash_deny` | SAFE 模式 + bash(ls) → DENY |
| `test_evaluate_normal_bash_dangerous` | NORMAL + 危险 → CONFIRM |
| `test_evaluate_rule_override` | 规则 allow rm → ALLOW |
| `test_evaluate_deny_priority` | 规则 deny ls + mode allow 它 → DENY |

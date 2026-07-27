---
status: accepted
date: 2026-07-27
---

# 0013: 权限系统

## 背景

Agent 需要安全防线，防止危险操作（rm -rf、fork bomb、网络请求）在用户不知情时执行。文档 §5.4 定义了 4 种权限模式、16+ 危险命令正则、三层规则体系和审计日志。

## 约束

1. **纯函数决策**——`evaluate(mode, rules, operation) → Decision` 无副作用，可脱离 Agent 单测
2. **决策中心化**——所有操作在 loop.py 的唯一执行点做权限检查，不分散在工具 handler 内
3. **可审计**——每次决策落轨迹 `EventType.permission_check` 事件
4. **按逆可性分档**——read（零副作用）> write/edit（可逆）> bash（不可逆）
5. **默认安全**——`normal` 模式为默认，`safe` 模式由用户显式降权

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| 交互模式 | A: 内部 confirm→allow/B: confirm→deny/C: 注入回调 | **C** |
| 危险命令分类位置 | A: tools.py handler 内 / B: loop.py 工具执行前 / C: 独立模块 | **C** |
| 危险命令正则维护 | A: 硬编码字符串 / B: `.agent/permit.toml` 配置文件加载 | **A（hardcoded + 扩展点）** |
| 规则文件格式 | A: `.agent/rules.toml` / B: 复用 `.agent/rules.md` | **A** |

## 架构

### 核心数据

```python
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
    SAFE = "safe"         # 只读命令
    DANGEROUS = "dangerous"  # 不可逆操作

@dataclass
class PermissionRule:
    pattern: str          # 操作匹配模式（glob over 工具名+路径）
    action: Decision      # allow / deny
    scope: str = "global" # "session" / "once"
```

### 危险命令分类

16+ 正则。安全档（safe）：只读命令如 `ls`, `git status`, `cat`, `head`, `tail`, `wc`, `echo`, `pwd`, `date`, `which`, `whoami`, `id`, `uname`, `env`, `find`（仅同名，非 find -exec）。危险档（dangerous）：`rm`, `rmdir`, `dd`, `>`, `>>`, `chmod`, `chown`, `mv`, `cp`, `ln`, `kill`, `reboot`, `shutdown`, `curl|sh`, `wget|sh`, `python -c`, `bash -c`, `sed -i`, `find -delete`, `fuser`, `mkfs`, `fdisk`, `exec`, eval 风格注入。

### 决策函数

```
evaluate(mode, rules, operation) → (Decision, matched_rule)
```

1. 首先检查规则表中是否有 allow/deny 匹配 → 若匹配，deny 优先于 allow
2. 若无规则命中 → 按模式表默认策略决策
3. 每次决策 emit `permission_check` 事件

### 交互回调

`Agent.__init__` 接受可选 `on_permission: Callable[[Operation, Decision], Decision]`。默认回调对 CONFIRM 返回 DENY（安全默认）。评测时注入 `lambda op, default: ALLOW`。

### 集成点

- `loop.py`：bash 的 handler 调用前插入 `evaluate()`。
- 默认模式注入 `SystemPrompt` 的权限段，告知 Agent 当前模式。
- 模式变更通过 `EventType.mode_change` 事件记录。

## Consequences

- 危险命令正则列表硬编码在模块内，通过 PR 更新。也可在 `.agent/rules.toml` 中追加用户自定义危险命令。
- 权限检查只对 `bash` 做，read/write 类在 safe 模式直接拒绝（不需 confirm），normal/autoedit 通过文件系统 ACL 兜底。

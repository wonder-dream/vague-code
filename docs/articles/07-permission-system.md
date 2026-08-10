# Permission System

**谁需要读：** 想理解 vague-code 安全模型和权限决策流程的开发者/用户
**前置阅读：** 06-context-engineering.md
**读完能做什么：** 选择合适的安全等级、编写自定义权限规则、理解审计日志

---

## 1. 概述

vague-code 的权限系统遵循一个设计哲学：**默认安全，渐进信任**。Agent 有修改代码和执行命令的能力，因此权限系统必须确保这些能力不会在用户不知情的情况下被滥用。

核心设计：
- **4 种模式**按操作可逆性切分信任等级——从"完全只读"到"最大自动"
- **三层规则体系**（全局持久 → 会话临时 → 单次豁免），DENY 最高优先级
- **决策函数为纯函数**（`permission.py:135-160`），每次决策落审计日志

ADR-0013 的设计动机：将安全性建模为一组可组合、可审计的决策规则，而不是一个简单的"开/关"开关。权限系统的每一次决策都被记录，用户可以随时回查 Agent 做了什么操作。

---

## 2. 4 种模式详解

**PermissionMode 枚举**（`permission.py:14-18`）：

| 模式 | 枚举值 | tag line | 信任等级 | 推荐场景 |
|------|--------|----------|---------|---------|
| safe | `"safe"` | 完全只读 | 最低 | 代码审查、学习理解 |
| normal | `"normal"` | 写需确认 | 中 | 日常开发（**默认**） |
| autoedit | `"autoedit"` | 写自动、命令需确认 | 较高 | 信任代码修改 |
| auto | `"auto"` | 最大自动 | 最高 | 评测跑分、夜间任务 |

**默认策略矩阵**（`permission.py:103-132`）：

| 操作类别 | safe | normal | autoedit | auto |
|---------|------|--------|----------|------|
| read（read_file/glob/grep） | ALLOW | ALLOW | ALLOW | ALLOW |
| write（write_file/patch） | DENY | CONFIRM | ALLOW | ALLOW |
| bash_safe（ls/git/echo/...） | DENY | CONFIRM | CONFIRM | ALLOW |
| bash_dangerous（rm/curl\|sh/...） | DENY | CONFIRM | CONFIRM | CONFIRM |

信任等级从 safe 到 auto 逐步递增：
- **safe**：只能读，任何写和命令执行都被拒绝
- **normal**：写操作需要你确认，安全命令也需要确认
- **autoedit**：写操作自动放行，但命令执行（即使是安全命令）仍需确认
- **auto**：仅危险命令仍需确认——这是唯一防线

---

## 3. 危险命令分类

bash 命令在执行前通过 `classify_bash()`（`permission.py:93-100`）分类为安全或危险：

```python
def classify_bash(command: str) -> DangerLevel:
    for pattern in DANGEROUS_PATTERNS:    # 先匹配危险
        if pattern.search(command):
            return DangerLevel.DANGEROUS
    for pattern in SAFE_PATTERNS:         # 再匹配安全
        if pattern.search(command):
            return DangerLevel.SAFE
    return DangerLevel.DANGEROUS          # 默认危险
```

**先匹配危险、后匹配安全、默认危险**的三段逻辑保证：新增的危险命令如果不小心没被危险模式捕获，至少会被默认策略挡住。

### 18 个安全命令（`permission.py:42-61`）

```
ls, git status/log/diff/branch/show/blame/grep,
cat, head, tail, wc, echo, pwd, which, whoami,
id, uname, env, date, printenv, type, cp, mv
```

### 24 个危险命令（`permission.py:63-87`）

```
rm, rmdir, dd, chmod, chown, ln, kill, killall, pkill,
reboot, shutdown, curl|sh, wget|sh, python -c, bash -c,
sed -i, find -delete, fuser, mkfs, fdisk, exec, eval,
>/dev/* (null/zero/random/urandom)
```

### 已知修复

- **B13 修复**：空 pattern 防护（`permission.py:143-144` `if not rule.pattern: continue`）——过滤规则文件中可能出现的空字符串 pattern，防止意外匹配所有操作
- **B14 修复**：cp/mv 从危险提升至安全（`permission.py:59-60`）——复制和移动文件在大多数场景下是可逆操作，不应归类为危险

---

## 4. 三层规则体系

**PermissionRule 数据类**（`permission.py:33-37`）：

```python
@dataclass
class PermissionRule:
    pattern: str         # 正则表达式，匹配 "tool_name {input}"
    action: Decision     # ALLOW / DENY
    scope: str = "global"
```

规则 pattern 匹配格式为 `"{tool_name} {input}"` 的连接字符串。例如 `bash rm` 匹配 bash 工具中包含 rm 的调用。

### 三层规则的存储与加载

| 层 | 存储位置 | 生命周期 | 加载时机 |
|---|---------|---------|---------|
| 持久规则 | `.agent/permission-rules.json` | 跨会话 | `app.py:81-88` → `agent.add_permission_rule()` |
| 会话规则 | 内存 `_permission_rules` | 单次 run | `agent.add_permission_rule()`（`loop.py:543-548`） |
| 单次豁免 | 内存 | 一次确认 | TUI 交互中 `Ctrl+Y` → 同时写入持久规则 |

### 匹配策略

```
op_repr = operation.tool_name + " " + str(operation.input)
for rule in rules:
    if not rule.pattern:
        continue
    if re.search(rule.pattern, op_repr):
        return rule.action   # DENY 最高优先级
```

**DENY 最高优先级**：即使有 ALLOW 规则匹配，只要 DENY 规则先命中就返回 DENY。这是安全底线——用户可以精确地禁止某些操作，不会被其他规则覆盖。

---

## 5. 决策函数

**evaluate()**（`permission.py:135-160`）是纯函数——不改全局状态、不写文件、不调外部服务。

```python
def evaluate(mode, operation, rules) -> Decision:
    # 第 1 步：规则匹配（最高优先级）
    if rules:
        for rule in rules:
            if re.search(rule.pattern, op_repr):
                return rule.action

    # 第 2 步：默认策略
    policy = DEFAULT_POLICIES[mode]

    # 第 3 步：bash 特殊处理
    if operation.tool_name == "bash":
        level = classify_bash(command)
        return policy["bash_safe" if level == "safe" else "bash_dangerous"]

    # 第 4 步：按工具类映射
    if operation.tool_name in ("read_file", "glob", "grep"):
        return policy["read"]
    if operation.tool_name in ("write_file", "patch"):
        return policy["write"]
    return policy["write"]  # 默认 → write 策略
```

四步决策流程从具体到抽象：
1. 用户自定义规则（最高优先级）
2. 默认策略表
3. bash 特殊分类
4. 按工具类型回退

### 与 Agent 的集成

`_check_tool_permission()`（`loop.py:498-541`）在工具执行前做 pre-pass：
- **DENY** → 直接生成 `ToolResultBlock(is_error=True)` + 错误消息，跳过执行
- **CONFIRM** → 回调 `_on_permission(op, decision)`
  - TUI：弹出 `PermissionDialog`（`app.py:_thread_permission`）；`write_file`/`patch` 先由 `vague_code/agent/prewrite.py` 计算写入前 diff 挂到 `op.review`，弹窗展示预览；拒绝理由（`op.feedback`）并入返回模型的错误消息
  - CLI：默认拒绝（无回调时）
- **ALLOW** → 通过，进入执行

**check_confirm 参数**（`loop.py:505,530,656`）：`_run_gen` 中为 True（正常执行时弹窗确认）；`_execute_pending_tools` 中为 False（resume 时不重复弹窗）。

---

## 6. 审计日志

**代码位置：** `loop.py:519-522`

每次权限决策都记录一条 `permission_check` 事件：

```python
traj.emit(EventType.permission_check, turn=turn, payload={
    "tool": block.name,
    "decision": decision.value,
    "command": (op.command or "")[:200],  # bash 命令截断至 200 字符
})
```

**审计日志字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| tool | str | 工具名 |
| decision | str | "allow" / "confirm" / "deny" |
| command | str | bash 命令截断至 200 字符，非 bash 工具为空 |

审计日志回答四个问题：**谁**（哪个工具）/ **什么操作**（输入参数）/ **什么判决**（ALLOW/CONFIRM/DENY）/ **为什么**（当前模式）。所有日志持久化在 SQLite 中，可通过事件流回查。

---

## 7. TUI 交互式确认

**PermissionDialog 交互流程**（`app.py:154-162`）：

1. Agent 调用 `_on_permission(op, decision=CONFIRM)`
2. TUI `_thread_permission()` 通过 `asyncio.run_coroutine_threadsafe` 桥接到主循环
3. `_show_permission_async()` 构造 `PermissionDialog` 并 `push_screen_wait`
4. 用户选择：
   - **Y**（允许一次）→ 返回 ALLOW，不做持久化
   - **Ctrl+Y**（始终允许）→ 返回 ALLOW + 持久化规则到 `.agent/permission-rules.json`
   - **N**（拒绝）→ 返回 DENY

**Ctrl+Y 持久化流程：**
1. 读取已有 rules JSON
2. append `{"pattern": "...", "action": "allow"}`
3. 写回文件
4. `agent.add_permission_rule(pattern, "allow")` → 当前会话立即生效

**持久规则文件格式**（`.agent/permission-rules.json`）：

```json
[
  {"pattern": "bash W*", "action": "allow"},
  {"pattern": "write_file *", "action": "allow"}
]
```

---

## 下一篇

→ **08-memory-system.md**：跨会话记忆系统——SQLite 统一记忆库、episodic 检索、增量蒸馏。

**相关 ADR：** 0013（Permission System）
**相关 plans：** 0011（permission-system）

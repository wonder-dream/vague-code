# 细纲：07-permission-system.md

**预估行数：** ~400 行
**定位：** 权限系统的完整设计。

---

## 开头

- **谁需要读：** 想理解 XClaw 安全模型和权限决策流程的开发者/用户
- **前置阅读：** 06-context-engineering.md
- **读完能做什么：** 选择合适的安全等级、编写自定义权限规则、理解审计日志

---

## 细纲

### 1. 概述（~30 行）

- 设计哲学：默认安全 → 渐进信任
- 4 种模式按可逆性切分信任等级
- 三层规则体系（全局持久 → 会话临时 → 单次豁免），DENY 最高优先级
- 决策函数为纯函数（`permission.py:135-160`），每次决策落审计日志
- ADR-0013 设计动机

### 2. 4 种模式详解（~60 行）

**`PermissionMode` 枚举（`permission.py:14-18`）：**

| 模式 | 枚举值 | tag line | 信任等级 | 推荐场景 |
|------|--------|----------|---------|---------|
| safe | `"safe"` | 完全只读 | 最低 | 代码审查、学习理解 |
| normal | `"normal"` | 写需确认 | 中 | 日常开发（**默认**） |
| autoedit | `"autoedit"` | 写自动、命令需确认 | 较高 | 信任代码修改 |
| auto | `"auto"` | 最大自动 | 最高 | 评测跑分、夜间任务 |

**默认策略矩阵（`permission.py:103-132`）：**

| 操作类别 | safe | normal | autoedit | auto |
|---------|------|--------|----------|------|
| read（read_file/glob/grep） | ALLOW | ALLOW | ALLOW | ALLOW |
| write（write_file/patch） | DENY | CONFIRM | ALLOW | ALLOW |
| bash_safe（ls/git/echo/...） | DENY | CONFIRM | CONFIRM | ALLOW |
| bash_dangerous（rm/curl\|sh/...） | DENY | CONFIRM | CONFIRM | CONFIRM |
| network | DENY | CONFIRM | CONFIRM | ALLOW |

**信任等级渐变：** safe → normal → autoedit → auto，逐步放开写和命令执行权限
**auto 模式的唯一防线：** 危险命令仍需要确认（`CONFIRM`）

### 3. 危险命令分类（~50 行）

**`classify_bash()`（`permission.py:93-100`）：**
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

**18 个安全命令正则（`permission.py:42-61 `）：**
```
ls, git (status/log/diff/branch/show/blame/grep),
cat, head, tail, wc, echo, pwd, which, whoami,
id, uname, env, date, printenv, type, cp, mv
```

**24 个危险命令正则（`permission.py:63-87 `）：**
```
rm, rmdir, dd, chmod, chown, ln, kill, killall, pkill,
reboot, shutdown, curl|sh, wget|sh, python -c, bash -c,
sed -i, find -delete, fuser, mkfs, fdisk, exec, eval,
>/dev/* (null/zero/random/urandom)
```

**已知修复：**
- B13 修复：空 pattern 防护（`permission.py:143-144` `if not rule.pattern: continue`）
- B14 修复：cp/mv 从危险提升至安全（`permission.py:59-60`）

### 4. 三层规则体系（~50 行）

**`PermissionRule` 数据类（`permission.py:33-37`）：**
```python
@dataclass
class PermissionRule:
    pattern: str         # 正则表达式，匹配 "tool_name {input}"
    action: Decision     # ALLOW / DENY
    scope: str = "global"
```

**三层规则：**

| 层 | 存储位置 | 生命周期 | 加载时机 |
|---|---------|---------|---------|
| 持久规则 | `.agent/permission-rules.json` | 跨会话 | `app.py:81-88` `_load_permission_rules()` → `agent.add_permission_rule()` |
| 会话规则 | 内存 `_permission_rules: list[PermissionRule]` | 单次 run | `agent.add_permission_rule()`（`loop.py:543-548`） |
| 单次豁免 | 内存 | 一次确认 | TUI 交互中 `Ctrl+Y` → 同时写入持久规则 |

**匹配策略（`permission.py:141-145`）：**
```python
op_repr = operation.tool_name + " " + str(operation.input)
for rule in rules:
    if not rule.pattern:
        continue
    if re.search(rule.pattern, op_repr):
        return rule.action   # DENY 最高优先级
```

**DENY 最高优先级：** 即使有 ALLOW 规则匹配，DENY 规则先命中即返回 DENY

### 5. 决策函数（~50 行）

**`evaluate()`（`permission.py:135-160`）——纯函数：**

```python
def evaluate(mode, operation, rules) -> Decision:
    # 第 1 步：规则匹配（最高优先级）
    if rules:
        for rule in rules:
            if re.search(rule.pattern, op_repr):
                return rule.action    # DENY 或 ALLOW

    # 第 2 步：默认策略
    policy = DEFAULT_POLICIES[mode]

    # 第 3 步：bash 特殊处理（安全/危险分类）
    if operation.tool_name == "bash":
        level = classify_bash(command)
        return policy["bash_safe" if level == "safe" else "bash_dangerous"]

    # 第 4 步：按工具类映射
    if operation.tool_name in ("read_file", "glob", "grep"):
        return policy["read"]         # read 类
    if operation.tool_name in ("write_file", "patch"):
        return policy["write"]        # write 类
    return policy["write"]            # 默认 → write 策略
```

**与 Agent 的集成（`loop.py:498-541`）：**
- `_check_tool_permission()` 在工具执行前做 pre-pass
- DENY → 直接生成 `ToolResultBlock(is_error=True)` + 错误消息
- CONFIRM → 回调 `_on_permission(op, decision)`
  - TUI：弹出 `PermissionDialog`（`app.py:154-162`）
  - CLI：默认拒绝（无回调时）
- ALLOW → 通过

**`check_confirm` 参数（`loop.py:505,530,656`）：**
- `_run_gen` 中 = True（正常执行时弹窗确认）
- `_execute_pending_tools` 中 = False（resume 时不重复弹窗）

### 6. 审计日志（~30 行）

**代码位置：** `loop.py:519-522`

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

**可审计性：** 谁（哪个工具）/ 什么操作（输入参数）/ 什么判决 / 为什么（模式名）

### 7. TUI 交互式确认（~40 行）

**`PermissionDialog` 交互流程（`app.py:154-162`）：**
1. Agent 调用 `_on_permission(op, decision=CONFIRM)`
2. TUI `_thread_permission()`（`app.py:142-152`）通过 `asyncio.run_coroutine_threadsafe` 桥接到主循环
3. `_show_permission_async()` 构造 `PermissionDialog` 并 `push_screen_wait`
4. 用户选择：
   - **`Y`（允许一次）** → 返回 ALLOW，不做持久化
   - **`Ctrl+Y`（始终允许）** → 返回 ALLOW + 持久化规则到 `.agent/permission-rules.json`
   - **`N`（拒绝）** → 返回 DENY
5. `Ctrl+Y` 持久化流程：
   - 读取已有 rules JSON → append `{"pattern": "...", "action": "allow"}` → 写回文件
   - `agent.add_permission_rule(pattern, "allow")` → 当前会话生效

**持久规则文件格式：**
```json
[
  {"pattern": "bash W*", "action": "allow"},
  {"pattern": "write_file *", "action": "allow"}
]
```

---

## 结尾

**下一篇推荐：** → 08-memory-system.md（跨会话记忆系统）
**相关 ADR：** 0013（Permission System）
**相关 plans：** 0011（permission-system）

---

## 本文件说明

这是文档 `07-permission-system.md` 的细纲（大纲）。实际写作时需确认 `tui/screens/permission.py` 中的 `PermissionDialog` 实现细节。

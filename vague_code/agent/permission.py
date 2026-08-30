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
    # type（Windows cat）不再免确认：type .env 可读密钥，必须走确认/拒绝（#25）
    r"^\s*cp\b",
    r"^\s*mv\b",
    # ── 2026-08-28 补（B4）：版本/只读查询不误伤 ──
    r"^\s*python\s+(-v|--version)\b",
    r"^\s*pip(3)?\s+--version\b",
    r"^\s*git\s+config\s+(--get|--list)\b",
    r"^\s*git\s+config\s+--global\s+--get\b",
    r"^\s*(cargo|go|npm|pnpm|bun|uv)\s+--version\b",
    r"^\s*go\s+version\b",
    r"^\s*npx\s+--version\b",
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
    # ── 2026-08-28 补盲（B4）：RCE / 下载执行 / 解码执行 / 写脚本执行 ──
    r"curl\s+\S+\s+(-o|--output)\s+\S+",
    r"wget\s+\S+\s+(-O|--output-document)\s+\S+",
    r"certutil\s+.*-decode\b",
    r"mshta\b",
    r"regsvr32\b",
    r"powershell.*(-enc|-encodedcommand)\b",
    r"powershell.*(IEX|Invoke-Expression)\b",
    r"base64\s+(-d|--decode)\b",
    r"python\s+\S+\.py\b",
    r"bash\s+\S+\.(sh|bash)\b",
    r"cmd\s+/c\s+\S+\.bat\b",
    r"powershell\s+(-File|-f)\b",
    r"call\s+\S+\.bat\b",
    # ── 阶段0.5（#15）：编码/下载执行显式正则（默认危险兜底之上双保险）──
    r"openssl\s+enc\s+-d\b",
    r"certutil\s+-urlcache\b",
    r"cmd\s+/v:?on\s+/c\b",
    r"%comspec%\s*/c\b",
    # ── 阶段0.6（#8）：cmd 控制结构内的危险操作（for/if 分支）──
    r"for\s+.*\b(rm|del|rd|rmdir|format|shutdown)\b",
    r"if\s+.*\(.*\b(rm|del|rd|rmdir|format|shutdown)\b",
    # ── 阶段1.3（#5）：bash 写系统敏感路径 ──
    r"[>]{1,2}\s*(/etc/|/usr/|/bin/|/sbin/|/var/|/dev/|/root/|/proc/|/sys/)",
    r"[>]{1,2}\s*c:\\windows",
    r"[>]{1,2}\s*c:/windows",
    # ── 阶段1.4（#16）：echo/printf 把危险命令写进脚本文件 ──
    r"(?:echo|printf)\s+.*\b(rm|dd|chmod|chown|curl|wget|mkfs|fdisk|shutdown|base64)\b.*[>]{1,2}\s*\S+\.(sh|bat|cmd|ps1|py|bash|zsh)",
    # ── 2026-08-28 补盲（B4）：Windows 进程 / 磁盘 / 系统 ──
    r"stop-process\b",
    r"wmic\s+process\b",
    r"sc\s+stop\b",
    r"shutdown\b",
    r"restart\b",
    r"diskpart\b",
    r"clear-disk\b",
    # ── 2026-08-28 补盲（B4）：包管理器执行/安装 ──
    r"cargo\s+(run|install|build)\b",
    r"go\s+(run|install)\b",
    r"uv\s+pip\s+install\b",
    r"pnpm\s+(install|add)\b",
    r"bun\s+(install|add)\b",
    r"npm\s+run\b",
    r"npx\s+(?!--version\b)",
    # ── 2026-08-28 补盲（B4）：git 写面 ──
    r"git\s+push\s+-f\b",
    r"git\s+remote\s+set-url\b",
    r"git\s+filter-branch\b",
    r"git\s+submodule\s+update\b",
    r"git\s+config\s+(?:--global|--local|--system)\s+[a-zA-Z]",
)

_SAFE_PATTERNS = [re.compile(p) for p in _SAFE_COMMANDS]
_DANGEROUS_PATTERNS = [re.compile(p) for p in _DANGEROUS_COMMANDS]

# 高危灾难命令（#2）：即使 auto 模式也强制二次确认。
_CRITICAL_PATTERNS = [
    re.compile(p)
    for p in (
        r"rm\s+-rf\s+/",
        r"\bdd\b",
        r"\bmkfs\b",
        r"\bfdisk\b",
        r"chmod\s+-r\s+777\s+/",
        r"curl\s+\S+\s*\|\s*(sh|bash|zsh)",
        r"wget\s+\S+\s*\|\s*(sh|bash|zsh)",
        r"\bdiskpart\b",
        r"format\s+[a-zA-Z]:",
        r"\bshutdown\b",
        r"\breboot\b",
    )
]

# 命令分隔符（plans/0020 B2）：拆段后逐段分类，堵住 `cat x; rm` / `dir & del` 等拼接绕行。
_CMD_SEPARATOR_RE = re.compile(r"[&|;\n]+")


def is_critical_bash(command: str) -> bool:
    """#2：高危灾难命令（rm -rf /、dd、mkfs、fdisk、curl|sh、关机等）。"""
    cmd = (command or "").lower()
    return any(p.search(cmd) for p in _CRITICAL_PATTERNS)


def _normalize_command(command: str) -> str:
    """规范化命令：小写 + 去引号 + 展开常见包装前缀（cmd /c、powershell -c 等）。

    只用于分类，不改变实际执行。
    """
    cmd = (command or "").lower()
    cmd = cmd.replace('"', "").replace("'", "").replace("`", "")
    # 展开包装前缀，使 `cmd /c Rm -rf /` → `rm -rf /`
    cmd = re.sub(
        r"^\s*(?:cmd(?:\.exe)?\s*/c\s*|"
        r"powershell\s+(?:-command|-c)\s*|"
        r"pwsh\s+(?:-command|-c)\s*|"
        r"bash\s+-c\s*)\s*",
        "",
        cmd,
    )
    return cmd.strip()


def _split_command_segments(command: str) -> list[str]:
    """按命令分隔符拆段，返回规范化后的非空片段。"""
    norm = _normalize_command(command)
    return [seg.strip() for seg in _CMD_SEPARATOR_RE.split(norm) if seg.strip()]


def _segment_dangerous(segment: str) -> bool:
    return any(p.search(segment) for p in _DANGEROUS_PATTERNS)


def _segment_safe(segment: str) -> bool:
    return any(p.search(segment) for p in _SAFE_PATTERNS)


def classify_bash(command: str) -> DangerLevel:
    """三段式分类：先危险、后安全、默认危险；并对命令拆段逐段判定（plans/0020 B2）。

    任一段命中危险 → 整体危险；全部段安全 → 安全；存在未知段 → 保守危险。
    """
    segments = _split_command_segments(command)
    if not segments:
        return DangerLevel.DANGEROUS
    all_safe = True
    for seg in segments:
        if _segment_dangerous(seg):
            return DangerLevel.DANGEROUS
        if not _segment_safe(seg):
            all_safe = False
    return DangerLevel.SAFE if all_safe else DangerLevel.DANGEROUS


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

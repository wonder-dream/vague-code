"""输出凭据脱敏（阶段2 #28）。

对工具输出统一做轻量正则脱敏，避免 API key / token / 密码等泄露到模型上下文。
可开关：REDACT_OUTPUT=False 关闭（测试/调试用）。
"""

from __future__ import annotations

import re

REDACT_OUTPUT = True

_SECRET_PATTERNS = (
    # key=value / key: value 形态
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|client[_-]?secret|access[_-]?key)\b\s*[:=]\s*\S+"),
    # 常见密钥前缀
    re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(ghp|github_pat)_[a-z0-9_]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def redact_secrets(text: str) -> str:
    """把疑似凭据替换为 ***；普通文本原样返回。"""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***", out)
    return out

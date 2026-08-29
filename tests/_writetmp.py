"""可写临时目录辅助（本环境受限 token 下 tempfile 创建的目录不可清理）。

改用 Path.mkdir 创建可写目录 + best-effort rmtree 清理，供各测试模块替代
`tempfile.TemporaryDirectory()` / `tempfile.mkdtemp()`。
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / ".writetmp_root"


def make_temp_dir(prefix: str = "tmp_") -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    p = _ROOT / f"{prefix}{uuid.uuid4().hex[:12]}"
    p.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def writable_temporary_directory(prefix: str = "tmp_"):
    """替代 tempfile.TemporaryDirectory：返回字符串路径，退出时 best-effort 清理。"""
    p = make_temp_dir(prefix)
    yield str(p)
    try:
        shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass

"""测试基础设施：覆盖 pytest 的 tmp_path fixture。

本环境（Windows 受限 token / 目录锁）pytest 默认 tmp_path 的 basetemp 创建与
清理会因 PermissionError 失败。这里用 Path.mkdir + best-effort rmtree 自管理
临时目录，保持与原 tmp_path 相同的 Path 语义，使全量回归可跑。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

_TEST_TMP_ROOT = Path(__file__).resolve().parent / ".pytest_tmp_root"


@pytest.fixture()
def tmp_path() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    p = _TEST_TMP_ROOT / f"pytest-{uuid.uuid4().hex[:12]}"
    p.mkdir(parents=True, exist_ok=True)
    yield p
    try:
        shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass

# 0023: bash 工具 python -c 输出乱码/丢失修复

- **日期**: 2026-08-09
- **状态**: approved（核心修复 + 超时加固，二次超时 10s）

## 问题

`_bash_factory`（src/agent/tools.py:250）Windows 上执行 `python -c`：

1. **主因（实锤）**：`chcp 65001` 前缀只改控制台代码页，python 管道模式（stdout=PIPE）忽略它，用 ANSI 代码页（GBK）。实测 `python -c "print('中文')"` 输出 `b'\xd6\xd0\xce\xc4\r\n'`（GBK），而 agent 用 `decode("utf-8")` → 中文全变 `�`，模型视为无输出。
2. **次因**：30s 超时 kill 进程树时 python 8KB 块缓冲未 flush 输出丢失；kill 后 `communicate()` 无二次超时，极端组合下挂起（复现 120s 未返回）。

## 修复

### src/agent/tools.py `_bash_factory`

```python
env = dict(os.environ)
env["PYTHONUTF8"] = "1"        # python 输出 UTF-8（实测 sys.stdout.encoding = utf-8）
env["PYTHONUNBUFFERED"] = "1"  # 无缓冲：超时杀进程不丢已输出内容
proc = subprocess.Popen(command, shell=True, cwd=cwd_path, env=env, ...)
```

### 超时加固（二次超时 10s）

kill 后 `communicate(timeout=10)`，二次超时用首次 `TimeoutExpired` 携带的部分输出兜底，不再无上限等待。

## 验证

- 复现脚本：env 注入后 `print('中文')` 输出合法 UTF-8 字节
- 超时场景：无缓冲输出保留；二次超时不挂起
- `pytest tests/test_tools.py -q` 全过
- ruff check

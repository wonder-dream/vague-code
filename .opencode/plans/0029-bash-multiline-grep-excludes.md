# 0029: bash 多行 python -c 修复 + grep 排除噪音目录

- **日期**: 2026-08-09
- **状态**: approved

## 问题（均已实测复现）

1. **多行 `python -c "..."` 空输出**：`shell=True` 经 cmd.exe 传递含换行的 `-c` 参数被破坏 → rc=0、stdout/stderr 全空。LLM 生成的多行验证脚本每次跑都"无输出"。
2. **grep 扫到 runs/ 噪音**：`_grep_factory` 的 rglob 无排除目录，runs/ 下轨迹 JSONL 被搜索命中。

## 修复

### src/agent/tools.py `_bash_factory`

检测 `python -c "..."` 且代码含换行 → 提取代码写入 `tempfile.gettempdir()/xclaw_<uuid>.py` → 命令改写为 `python "tmp.py"` → 执行后 finally 删除。提取失败/无换行保持现状。

### grep/glob 排除噪音目录

共享排除集合：`.git .venv __pycache__ .mypy_cache .pytest_cache .ruff_cache node_modules runs .opencode eval/.venvs`，rglob 时跳过。

## 测试

- 多行 python -c（for 循环）输出正常 + 临时文件清理
- 单行命令不受影响
- grep 排除 runs/
- 全量回归 + ruff + mypy

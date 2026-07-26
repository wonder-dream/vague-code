# Batch 01: Critical Crash Paths

## C1 — tiktoken 模块级导入崩溃

**问题**：
`context_tokens.py` 在模块级执行 `import tiktoken` 和 `tiktoken.get_encoding()`。
若 tiktoken 未安装（pip install 失败、被卸载、环境切换等），整个模块不可导入，
Agent 在第一次 LLM 调用前全面崩溃。

**修复方式**：
tiktoken 改为延迟加载（首次调用 `count_tokens` 时才初始化），降级方案为 char/4 估算。
降级时不影响 Agent 正常运行，仅 token 计数精度从 ±3% 降为 ±15%。

**疑点**：pyproject.toml 已声明依赖，卸载概率低。但模块级 import 在测试和代码分析工具
（如 `pytest --collect-only`、`import src.agent.loop` 等）中也触发，不应崩溃。

## C2 — token 计账在 retry try/except 之外

**问题**：
`count_tokens()` 和 `compute_budget()` 在 `_run_gen()` 的 while 循环外调用，
处于 LLM 调用的 retry try/except 之外。若这两个函数抛异常（tiktoken 未装、编码错误等），
异常穿透 `_run_gen` → `RunHandle` → Agent 无恢复能力。

**修复方式**：
把 token 计账代码移入 LLM 调用的 try/except 块内。异常时走 `classify_llm_error`
→ `codec_error`（不可重试）→ `run_end(llm_error)`，实现 graceful degradation。

## C3 — count_tokens 未处理 tools 列表中的 None

**问题**：
`count_tokens()` 遍历 `tools` 列表时未检查 None 条目，`t.description` 在 t 为 None
时抛 `AttributeError`。虽然 `self._tool_specs` 正常情况不会含 None，但防御性编码要求
不假设数据永远合法。

**修复方式**：
在遍历循环中加 `if t is None: continue`。

## 风险

- char/4 降级在 token 精度敏感场景下有误差风险，但 C1 的崩溃优先级高于精度。
  利用率阈值判断的误差在后续压缩实现中可以容忍。
- C2 的改动是纯代码移动，不改变业务逻辑，风险极低。
- C3 是 1 行 guard，无风险。

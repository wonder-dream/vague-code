# Batch 02: Token Count Correctness & Rules File Robustness

## H2 — count_tokens 只统计 TextBlock，忽略工具输入输出

**问题**：
`count_tokens()` 的 `_count_precise()` 和 `_count_rough()` 在遍历 `blocks` 时只对 `TextBlock`
做 token 计数。`ToolUseBlock.input`、`ToolResultBlock.content`、`ThinkingBlock.text` 均被跳过。
在含多轮工具调用的对话中，这些被跳过的内容占 token 总量的 >80%，导致 utilization 严重低估。

**修复方式**：
在循环中增加 `isinstance` 分支，对三种 Block 类型分别计数。

**边界**：
- `ToolUseBlock.input` 是 dict，用 `json.dumps` 序列化后计数
- `ToolResultBlock.content` 是 str，直接计数
- `ThinkingBlock.text` 是 str，直接计数
- DeepSeek codec 会丢弃 ThinkingBlock，但计数时仍然计入——宁可偏大不可偏小

## H3 — 二进制 `.agent/rules.md` 导致崩溃

**问题**：
`load_rules()` 的 `f.read_text(encoding="utf-8")` 遇到二进制/非 UTF-8 文件时抛
`UnicodeDecodeError`，穿透 `SystemPrompt.build()` → `Agent.start()` → 不可恢复。

**修复方式**：
提取 `_safe_read()` 辅助函数，try/except `UnicodeDecodeError` 后返回 `None`，
调用方跳过不追加。

## H4 — 规则文件无体量限制

**问题**：
`load_rules()` 对文件大小、文件数量、层级深度全部无上限。10MB 单文件 + 1000 层目录
可以耗尽上下文窗口。

**修复方式**：
新增三个模块级常量做门禁：`MAX_RULES_SIZE=10KB`、`MAX_RULES_FILES=20`、`MAX_RULES_DEPTH=50`。

## 风险

- H2 不会改变现有测试结果，仅使计数更接近真实值。
- H3+H4 都是守卫加固，不改变正常路径的行为。

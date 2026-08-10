# 0036: /compact 展示摘要（对齐 opencode）

- **日期**: 2026-08-10
- **状态**: approved（用户确认）

## 背景

opencode 的 /compact 压缩后把 LLM 摘要作为一条独立 assistant 消息写入会话，
TUI 对话流自然展示。vague-code 的 `compact_chat()` 已生成摘要文本
（`detail.summary_text`，0035 起为 Pi 风格结构化摘要），但 TUI 只显示
"已压缩：回收 X.Xk tokens"，摘要内容被丢弃。

## 改动

1. **`loop.py` `compact_chat()`**：返回值加 `"summary"` 键（从 auto_compact
   report 的 `detail.summary_text` 提取，空则 "")
2. **`app.py` `_run_compact_worker`**：压缩成功且 summary 非空时，以
   `[会话摘要]` 标题 + 摘要正文写入 transcript（ASSISTANT 样式 markdown 渲染，
   对齐 opencode "摘要=对话消息"）
3. **测试**：`test_chat_session.py` 断言 compact_chat 返回 summary；
   TUI 测试断言摘要条目出现在 transcript

## 验证

全量 pytest + ruff/mypy + 提交发布。

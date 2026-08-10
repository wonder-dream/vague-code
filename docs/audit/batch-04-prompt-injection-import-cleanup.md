# Batch 04: Prompt Injection Prevention & Import Cleanup

## #1 — M1: 系统提示注入防护

**问题**：
`SystemPrompt.build()` 把 `.agent/rules.md` 内容直接拼接进系统提示，无任何边界标记。
"Ignore all previous instructions" 类注入文本可以被 LLM 当作系统级指令执行。

**修复**：
用代码块包裹 + 限定语标记规则段，提示 LLM 规则内容的层级（用户偏好 < 核心指令）。

## #2 — M5: trajectory.py 延迟导入提到顶部

**问题**：
`to_messages()` 内部有 `from vague_code.agent.context import SystemPrompt` 延迟导入。
如果导入失败（循环依赖、模块未安装等），resume 路径崩溃。

**修复**：
移入文件顶部的模块级导入，与已有导入放一起。`context.py` 不依赖 `trajectory.py`，
无循环导入风险。

## #3 — M6: bash chcp 前缀碰撞

**问题**：
命令以 `chcp` 开头时，当前无条件前缀追加产生
`chcp 65001 >nul && chcp 65001` 双重设置。

**修复**：
检测命令是否已以 `chcp` 开头，是则跳过前缀注入。

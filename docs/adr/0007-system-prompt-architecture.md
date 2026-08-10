---
status: accepted
date: 2026-07-26
---

# 0007: System Prompt Architecture

## 背景

当前 Agent 发送给 LLM 的消息数组是 `[user(task+workdir)]`，没有任何系统提示。
从实际运行看（见 fix/workdir-context），LLM 不知道自己的工作目录，会猜测 `/workspace` 等不存在路径。

系统提示是在每个 LLM 调用前附加的一组指令性文本，定义 Agent 的身份、行为规范和工作区上下文。
本 ADR 覆盖系统提示的三段结构、消息注入方式、codec 适配和 resume 路径兼容。

## 约束

1. **Anthropic 协议废弃 `messages[0].role="system"`**（deprecated），要求系统提示走 `system` 顶层参数
2. **DeepSeek / OpenAI 协议要求 `messages[0].role="system"`**，无 `system` 顶层参数
3. **resume 路径必须重建与 start 路径相同的系统提示**（已通过 ADR-0006 建立 `Trajectory.from_db` + `to_messages()` 恢复机制）
4. **静态部分应在消息序列的前部**，利用 LLM 服务端的 prefix KV cache 优化

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| System prompt 在 IR 中的表达 | A: 新增 `Message.role="system"` / B: 塞进第一条 user 消息 | A |
| DeepSeek codec 编码 | A: wire format `role="system"` / B: 直接拼接 `role="user"` | A |
| Anthropic codec 编码 | A: `body["system"]` 参数 / B: `messages[0].role="system"`（deprecated） | A |
| 模板结构 | A: 三段静态拼接 / B: `Section` 对象 + 优先级排序 | A |

## 决策

### 1. IR 层：Message 新增 system 角色

`vague_code/agent/ir.py`：

```python
# 改前
role: Literal["user", "assistant"]

# 改后
role: Literal["user", "assistant", "system"]
```

`Message.__init__` 已有 `isinstance(content, str) → [TextBlock(text=content)]` 逻辑，system 消息使用纯文本字符串即可。

### 2. 三段模板

```python
[identity]  →  "You are Xcode, a coding agent..."
[rules]     →  从 .agent/rules.md 加载或空
[session]   →  "Workspace root: {workdir}"
```

三段按 identity → rules → session 顺序拼接。
identity 是硬编码的常量，rules 和 session 可能变化。identity 在 KV cache 中永不变，

### 3. Codec 适配

**DeepSeek codec**（`encode_request`）：

```
messages 中含有 Message(role="system") → 
  wire message 追加 {"role": "system", "content": text}
```

**Anthropic codec**（`encode_request`）：

```
messages 中剥离所有 system 消息 →
  拼入 body["system"] = concatenated text
  其余 user/assistant 消息按原逻辑编码
  role="system" 消息不参与 merge_consecutive 和首条校验
```

### 4. Loop 侧集成

```python
# loop.py start() 中
system_prompt = SystemPrompt(workdir).build()
messages = [
    Message(role="system", content=system_prompt),
    Message(role="user", content=task),
]
```

### 5. Resume 适配

`to_messages()` 遇到 `run_start` 事件时，从 `payload.workdir` 重建 system message：

```python
if ev.type == EventType.run_start:
    workdir = ev.payload.get("workdir", "")
    if workdir:
        messages.append(Message(role="system", content=SystemPrompt(workdir).build()))
    task = ev.payload.get("task", "")
    messages.append(Message(role="user", content=task))
```

原来的 `to_messages()` 第一条 user 消息的 `"Workspace root: {workdir}\n\n{task}"` 前缀
取消——workdir 信息已移到 system prompt 中。

## Consequences

- 系统提示复用标准 IR 的 Message 类型，新增 `"system"` 角色仅一行类型定义
- 两个 codec 按各自协议的最佳实践编码：DeepSeek 走 message 数组，Anthropic 走 `system` 参数
- identity 段在 KV cache 中获得最佳命中率（跨请求完全相同、位置固定）
- resume 路径与 start 路径一致，使用 `SystemPrompt.build(workdir)` 统一构造
- 后续增加全局/项目规则时只需改 `SystemPrompt.build()` 方法，codec 和 loop 不变

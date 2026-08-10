# 0007: System Prompt Layer Injection

实现分层系统提示注入 + 规则文件层级加载 + token 预算记账。

---

## 设计原则

- **三文件裂变**：系统提示、规则加载、token 计数各一文件，互不耦合
- **IR 原生语义**：system 消息走 `Message.role="system"`，codec 按厂商协议映射
- **每轮记账**：token 计数是免费的消融数据，做不做压缩都要记
- **向下兼容**：resume 通过 `to_messages()` 重建 system 消息

---

## 文件清单

| 步骤 | 文件 | 操作 |
|------|------|------|
| 1 | `pyproject.toml` | 改：加 `tiktoken>=0.8.0` |
| 2 | `vague_code/agent/ir.py` | 改：`role` 支持 `"system"` |
| 3 | `vague_code/agent/context_rules.py` | **新建** |
| 4 | `vague_code/agent/context_tokens.py` | **新建** |
| 5 | `vague_code/agent/context.py` | **新建** |
| 6 | `vague_code/agent/codecs/deepseek.py` | 改：system role 编码 |
| 7 | `vague_code/agent/codecs/anthropic.py` | 改：system 剥离 → `body["system"]` |
| 8 | `vague_code/agent/loop.py` | 改：注入 system + budget |
| 9 | `vague_code/agent/trajectory.py` | 改：resume 重建 system |
| 10 | `tests/test_context_rules.py` | 新建 |
| 11 | `tests/test_context_tokens.py` | 新建 |
| 12 | `tests/test_context.py` | 新建 |
| 13 | `tests/test_deepseek_codec.py` | 改：system 编码测试 |
| 14 | `tests/test_agent_loop.py` | 改：system 注入测试 |
| 15 | `tests/test_anthropic_codec.py` | 改：system 参数测试 |

---

## 步骤 1：`pyproject.toml`

```toml
dependencies = [
    "tiktoken>=0.8.0",
    ...
]
```

## 步骤 2：`ir.py`

```python
# L88 改前
role: Literal["user", "assistant"]

# 改后
role: Literal["user", "assistant", "system"]
```

`Message.__init__` 已有字符串→TextBlock 转换，无需额外改动。

## 步骤 3：`context_rules.py`

```python
from __future__ import annotations
from pathlib import Path

RULES_FILENAME = ".agent/rules.md"

def load_rules(workdir: str | Path) -> str:
    root = Path(workdir).resolve()
    rules: list[str] = []
    for parent in reversed(root.parents):
        f = parent / RULES_FILENAME
        if f.is_file():
            rules.append(f.read_text(encoding="utf-8"))
    f = root / RULES_FILENAME
    if f.is_file():
        rules.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(rules)
```

## 步骤 4：`context_tokens.py`

```python
from __future__ import annotations
import json
import tiktoken
from vague_code.agent.ir import TextBlock, ToolSpec

CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 64_000,
    "claude-opus-4-8": 200_000,
}

_ENC = tiktoken.get_encoding("cl100k_base")

def count_tokens(messages: list, tools: list[ToolSpec] | None = None) -> int:
    total = 0
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else msg
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if isinstance(block, TextBlock):
                total += len(_ENC.encode(block.text))
    if tools:
        for t in tools:
            total += len(_ENC.encode(t.description))
            total += len(_ENC.encode(json.dumps(t.parameters)))
    return total

def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    window = CONTEXT_WINDOWS.get(model, 64_000)
    budget = int(window * 0.9)
    if user_max_tokens is not None:
        budget = min(budget, user_max_tokens)
    return budget
```

## 步骤 5：`context.py`

```python
from __future__ import annotations
from pathlib import Path
from vague_code.agent.context_rules import load_rules

class SystemPrompt:
    AGENT_IDENTITY = (
        "You are Xcode, a coding agent. "
        "Your task is to read, understand, modify, and test code.\n"
        "Always read a file before editing it. "
        "Run tests after making changes to verify correctness.\n"
        "Use glob/grep to explore unfamiliar codebases before making edits."
    )

    def __init__(self, workdir: str | Path) -> None:
        self._workdir = Path(workdir).resolve()

    def build(self) -> str:
        parts: list[str] = [self.AGENT_IDENTITY]
        rules = load_rules(self._workdir)
        if rules:
            parts.append(f"\nProject rules:\n{rules}")
        parts.append(f"\nWorkspace root: {self._workdir}")
        return "\n".join(parts)
```

## 步骤 6：`deepseek.py`

在 `encode_request` 的消息遍历循环中添加 system 分支。

```python
# L40-46 改前
for msg in messages:
    if msg.role == "assistant":
        wire_messages.append(_encode_assistant(msg))
    elif msg.role == "user":
        wire_messages.extend(_encode_user(msg))
    else:
        raise ValueError(f"unsupported role: {msg.role}")

# 改后
for msg in messages:
    if msg.role == "assistant":
        wire_messages.append(_encode_assistant(msg))
    elif msg.role == "user":
        wire_messages.extend(_encode_user(msg))
    elif msg.role == "system":
        wire_messages.append({"role": "system", "content": "".join(
            b.text for b in msg.content if isinstance(b, TextBlock)
        )})
    else:
        raise ValueError(f"unsupported role: {msg.role}")
```

需要 import `TextBlock`（当前 deepseek.py 已有）。

## 步骤 7：`anthropic.py`

在 `encode_request` 开头剥离 system 消息。

```python
# 在合并和校验之前插入

system_parts: list[str] = []
non_system: list[Message] = []
for msg in messages:
    if msg.role == "system":
        system_parts.extend(
            b.text for b in msg.content if isinstance(b, TextBlock)
        )
    else:
        non_system.append(msg)

messages = non_system  # 覆盖原变量，后续代码用 non_system

# 拼 body 时追加
if system_parts:
    body["system"] = "\n".join(system_parts)
```

同时需要修改首条校验逻辑：跳过 system 消息后，第一条非 system 消息必须为 user。

## 步骤 8：`loop.py` — 注入 system

```python
# L181 改前
messages = [Message(role="user", content=f"Workspace root: {workdir}\n\n{task}")]

# 改后
system_prompt = SystemPrompt(workdir).build()
messages = [
    Message(role="system", content=system_prompt),
    Message(role="user", content=task),
]
```

每轮前加入 token 记账：

```python
# 在 _run_gen() 的 turn 循环中，约 L195
total = count_tokens(messages, self._tool_specs)
budget = compute_budget(self.config.model)
traj.emit(EventType.compression, turn=turn, payload={
    "layer": "budget",
    "before_tokens": total,
    "after_tokens": total,
    "budget": budget,
    "utilization": round(total / budget, 4),
    "tools_tokens": count_tokens([], self._tool_specs),
    "tool_count": len(self._tool_specs),
})
```

需在 `trajectory.py` 的 `EventType` 中加 `compression`。

## 步骤 9：`trajectory.py`

```python
# EventType 加枚举值
compression = "compression"

# to_messages() 中 run_start 处理改后
if ev.type == EventType.run_start:
    if messages and messages[-1].role == "user":
        continue
    workdir = ev.payload.get("workdir", "")
    if workdir and not any(m.role == "system" for m in messages):
        sys_text = SystemPrompt(workdir).build()
        messages.append(Message(role="system", content=sys_text))
    task = ev.payload.get("task", "")
    messages.append(Message(role="user", content=task))
```

记得去掉 `f"Workspace root: {workdir}\n\n{task}"` 前缀，恢复为 `task`。

## 步骤 10~15：测试

| 文件 | 测试函数 | 验证点 |
|------|----------|--------|
| `test_context_rules.py` | `test_no_rules_returns_empty` | 无规则文件返回空 |
| | `test_single_rule_at_workdir` | 单个规则文件 |
| | `test_hierarchical_merging` | 层级合并顺序 |
| `test_context_tokens.py` | `test_empty_messages_zero` | 空消息计数 |
| | `test_system_message_counted` | system 消息计数 |
| | `test_tools_included` | 工具定义计数 |
| | `test_compute_budget_known_model` | DeepSeek budget |
| `test_context.py` | `test_identity_section_present` | identity 出现在输出中 |
| | `test_session_includes_workdir` | workdir 在输出中 |
| | `test_rules_appended_if_present` | 规则文件接入 |
| `test_deepseek_codec.py` | `test_encode_system_message` | system→wire |
| `test_anthropic_codec.py` | `test_encode_system_message` | system→body["system"] |
| `test_agent_loop.py` | `test_start_includes_system` | 首条消息为 system |

---

## 执行顺序

1. pyproject — 安装 tiktoken
2. ir.py — 类型改动
3~5. context_rules / context_tokens / context.py — 三个新文件
6~7. codec 适配 — deepseek + anthropic
8~9. loop + trajectory — 集成
10~15. 测试 — 全部新建和补充

每步完成后 `pytest tests/` 验证不打破现有测试。最后全量 `pytest + ruff + mypy`。

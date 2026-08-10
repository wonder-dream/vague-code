---
status: accepted
date: 2026-07-26
---

# 0010: Context Module Architecture

## 背景

系统提示构建、规则文件加载、token 计数、后续的 stale_snip / microcompact / auto_compact / truncation
全部属于"上下文治理"范畴。需要一个独立的模块容纳这些逻辑，而不是塞进 loop.py 或 tools.py。

拆分决策影响模块内部耦合、测试便捷性和 Week 3 记忆系统的复用路径。

## 约束

1. **不依赖 `loop.py`、`tools.py`、`config.py`、`backend.py`**——上下文模块可以独立导入
2. **三个子功能必须可独立单测**——prompt 构建（无 tokenizer）、规则加载（无 io 以外的依赖）、token 计数（需 ir + tiktoken）
3. **Week 3 的记忆系统需要复用规则加载和 token 计数**
4. **文件最少化，不因拆分增加不必要的 import 链**

## Considered Options

| 决策点 | Options | 选出方案 |
|--------|---------|----------|
| 模块数量 | A: 1 个文件（context.py） / B: 3 个文件（拆分规则和 token） | B |
| 系统提示接入 loop.py 的方式 | A: loop.py 直接 import SystemPrompt / B: 通过 AgentConfig 间接传递 | A |
| Token 计数参数 | A: `count_tokens(messages, tools) → int` / B: 做成 StreamEvent 的 listener | A |
| 与压缩流水线关系 | A: context.py 也负责压缩 / B: 压缩单独文件引入 context_tokens | A |

## 决策

### 1. 三个文件

```
vague_code/agent/
  context.py          → SystemPrompt 类 + build() 方法
  context_rules.py    → load_rules(workdir) 纯函数
  context_tokens.py   → count_tokens(messages, tools) 纯函数
```

**为什么拆**：

| 文件 | 依赖 | 单测复杂度 | Week 3 复用 |
|------|------|-----------|-------------|
| `context.py` | `pathlib.Path` + 引用另两个模块 | 无 tokenizer，纯文本拼接 | 不复用 |
| `context_rules.py` | `pathlib.Path` | 需要临时目录创建假规则文件 | 复用（记忆系统加载同上行规则） |
| `context_tokens.py` | `ir.Block / TextBlock` + `tiktoken` | 需要 mock 消息 | 复用（记忆检索热度排序需 token 估算） |

**不拆的代价**：context.py 会随着 Week 2 的压缩流水线增长到 500+ 行。拆分后每个文件的职责不超过 150 行。

### 2. 模块合约

**context_rules.py**：

```python
def load_rules(workdir: str | Path) -> str:
    """层级遍历 .agent/rules.md，返回合并后的规则文本。"""
```

纯函数。无状态。无配置依赖。
输出直接拼接进系统提示的 `[rules]` 段。

**context_tokens.py**：

```python
from vague_code.agent.ir import Message, ToolSpec

CONTEXT_WINDOWS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro":    64_000,
    "claude-opus-4-8":   200_000,
}

def count_tokens(messages: list[Message], tools: list[ToolSpec] | None = None) -> int:
    """返回 messages + tools 定义折算的 token 总数。"""

def compute_budget(model: str, user_max_tokens: int | None = None) -> int:
    """返回该模型的预算上限。"""
```

依赖 `ir` 模块的 `Message` 和 `ToolSpec`——这是整个代码库中最稳定的两个类型。
单测通过构造 `Message(role="user", content="hello")` 即可。

**context.py**：

```python
from vague_code.agent.context_rules import load_rules
from vague_code.agent.context_tokens import count_tokens, compute_budget

class SystemPrompt:
    AGENT_IDENTITY = "..."

    def __init__(self, workdir: str | Path):
        self._workdir = Path(workdir).resolve()

    def build(self) -> str:
        rules = load_rules(self._workdir)
        parts = [self.AGENT_IDENTITY]
        if rules:
            parts.append(f"\nProject rules:\n{rules}")
        parts.append(f"\nWorkspace root: {self._workdir}")
        return "\n".join(parts)
```

### 3. loop.py 接入方式

```python
from vague_code.agent.context import SystemPrompt
from vague_code.agent.context_tokens import count_tokens, compute_budget

# start() 中
system_prompt = SystemPrompt(workdir).build()
messages = [Message(role="system", content=system_prompt), Message(role="user", content=task)]

# 每轮调用前
total = count_tokens(messages, self._tool_specs)
```

不使用注入框架或配置传递——loop.py 直接 import context 模块。理由：
- 无循环依赖（context.py 不 import loop）
- 模块职责单向：loop → context，不会反向
- 单测 mock 点清晰：mock `SystemPrompt.build()` 或 `count_tokens()` 即可

### 4. 压缩流水线的位置

`context.py` 也作为压缩流水线的入口：

```
context.py:
  - SystemPrompt.build()        # 已有
  - compress_chain()            # stale_snip → microcompact → auto_compact → truncation

context_compress.py:            # Week 2 后三新增
  - stale_snip(messages)        # 层 1
  - microcompact(messages)      # 层 2
  - auto_compact(messages)      # 层 3
  - truncate(messages)          # 层 4
```

今日先建 context.py + context_rules.py + context_tokens.py 三个文件。
压缩文件在具体实现时再引入，不提前创建空文件。

## Consequences

- context.py 系统提示构建与上下文压缩两个职责共用同一个模块入口，保持使用者（loop.py）的 import 简洁
- 三个文件总共 ~120 行代码，测试可在 80 行内覆盖全部场景
- Week 3 记忆系统可直接 import `context_rules.load_rules()` 和 `context_tokens.count_tokens()`
- `compute_budget()` 的静态映射表需要在模型增加时更新，已知限制

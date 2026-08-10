---
status: accepted
date: 2026-07-21
---

# 工具系统采用可注入注册表 + bind(workdir) 工厂模式

工具不内嵌于 Agent Runtime（loop.py），独立为 `vague_code/agent/tools.py` 模块。工具抽象为 `Tool` dataclass：`spec: ToolSpec`（JSON Schema 声明）+ `factory: (workdir) → (input) → str`（绑定工作目录后产出无状态 handler）。Agent 通过构造函数注入工具注册表 `dict[str, Tool]`（默认 `DEFAULT_TOOLS`），与 backend 注入同一手法——Agent 核心只见抽象，不见具体工具实现。

`bind(workdir)` 在 `run()` 启动时执行一次（workdir 在一次 run 内不变），产出的 handler 闭包捕获 workdir，此后工具调用签名统一为 `handler(input: dict) -> str`，不含环境参数。

## Considered Options

- **工具定义内嵌 loop.py（被否决）**：Agent Runtime 与 Tool System 耦合；加工具必须改 loop 本体；`if name == "read_file"` 分支随工具数量膨胀；权限/并发阶段需要的资源元数据无挂载点。
- **handler 签名 `(input, workdir)` 每次透传（被否决）**：workdir 在一次 run 内不变，逐次传递是噪声；且 handler 有状态泄露风险（调用方可以传不同的 workdir），放弃签名简洁性换取虚无的灵活性。
- **工厂 bind 一次、handler 纯 input（选定）**：绑定时机与 run 生命周期对齐；handler 签名最简；workdir 一致性由构造保证。

## 注册表设计

```python
@dataclass
class Tool:
    spec: ToolSpec
    factory: Callable[[str], Callable[[dict], str]]  # workdir → (input → content)

    def bind(self, workdir: str) -> Callable[[dict], str]:
        return self.factory(workdir)


DEFAULT_TOOLS: dict[str, Tool] = {"read_file": Tool(spec=..., factory=_read_file_factory)}
```

注入校验：`__init__` 检查每个 `key == tool.spec.name`，不一致即 `ValueError`。

## 异常处理决策

| 时机 | 场景 | 处理 | 事件 |
|------|------|------|------|
| 注册表注入 | key != spec.name | `ValueError`（构造期崩溃，fail-fast） | 无 |
| bind 期 | factory 抛异常 | catch → emit + terminate | `error(kind="tool_bind_error")` → `run_end(reason="tool_bind_error")` |
| handler 执行期 | 工具逻辑抛异常 | catch → `ToolResultBlock(is_error=True)` 回喂 | `tool_result(is_error=True)` |
| handler 协议违规 | 模型声明 tool_use 但发空批次 | catch → emit + terminate | `error(kind="empty_tool_use")` → `run_end(reason="empty_tool_use")` |
| handler 返回值 | handler 返回非 str | 不做强转；persist 序列化时暴露 | 归于 persist 失败路径 |
| 空注册表 | 用户注入 `tools={}` | 模型收到 `tools=[]`，不会主动调工具；若仍发 tool_use → 未知工具路径 | `tool_result(is_error=True)` |

## Consequences

- 新增工具 = 新增一个 `Tool` 实例并注册到 `DEFAULT_TOOLS`，Agent 核心零改动；
- 权限系统的资源 scope 元数据、并发调度的 R/W 语义字段后续挂在 `Tool` 上，结构已留挂点；
- `run_end` reason 集合扩展：`end_turn` / `max_turns` / `max_tokens` / `content_filter` / `unknown` / `llm_error` / `llm_timeout` / `tool_bind_error` / `empty_tool_use`；
- 测试可注入 FakeTool 验证注册表机制，无需文件系统副作用。

---
status: accepted
date: 2026-07-22
---

# 0005: 流式事件统一模型（StreamEvent IR）

## 背景

当前模型抽象层（ADR-0002）只支持非流式：`Backend.complete()` 返回聚合的 `ModelResponse`。
流式输出是实现实时 CLI 渲染、控制实验变量（评测需要同时支持流式/非流式）的前提。

本 ADR 覆盖 Week 1 路线的"流式 / StreamEvent IR"项，并指导后续 Week 2 上下文压缩、Week 3 权限交互的流式集成。

## Considered Options

| 决策点 | 选项 | 选出方案 |
|--------|------|----------|
| StreamEvent 角色 | A: 不统一，codec 各自处理 / B: 统一中间层 | B |
| thinking/reasoning 事件 | A: 合并到 text_delta / B: 独立 | B |
| 流错误处理 | A: 抛异常 / B: emit error 事件 | B |
| MessageEnd 信息 | A: 仅 stop_reason / B: 完整携带 finish_reason, truncated | B |
| tool args 缓冲 | A: codec 层 / B: Loop 层 | B |
| 截断工具调用 | A: 以空参数继续 / B: 抛异常 | B |
| JSON 解析失败 | A: fail-fast 抛异常 / B: is_error 回喂 | B |
| 多 tool 并行 | A: 隐式状态机 / B: 按 id 显式区分 | B |
| message_start 带预算 | A: 带 tokenizer 估算 / B: 不带，message_end 报实际 | B |
| ThinkingBlock signature | A: 不加 / B: 加 Optional 字段 | B |
| Thinking 边界 | A: 隐式靠事件类型切换 / B: 显式 ThinkingStart/ThinkingEnd | B |
| Backend 扩展 | A: 仅加方法 / B: stream() 新方法 + Loop 适配 | B |
| Loop 消费模式 | A: 两套分支 / B: 统一 Iterator[StreamEvent] | B |
| StreamEvent 鉴别 | A: enum discriminator / B: dataclass + isinstance | B |
| 轨迹粒度过流事件 | A: 只存聚合 / B: 全量存 + aggregated golden | B |
| Codec 职责边界 | A: 含 SDK 调用 / B: 纯翻译 + Backend 调 SDK | B |

## 决策

### 1. 架构：统一 StreamEvent IR + 每个 codec 映射

StreamEvent 作为中间层，各 codec 将厂商流式 chunk 翻译为统一事件。上层（Agent Loop、CLI、ContextManager）只消费 `Iterator[StreamEvent]`，零分支。

### 2. StreamEvent 类型（9 种）

采用 dataclass + isinstance 分派，与现有 IR 风格一致。

| 事件 | 字段 | 含义 |
|------|------|------|
| `MessageStart` | `model: str` | 本轮开始 |
| `ThinkingStart` | — | 思考块开始 |
| `ThinkingDelta` | `delta: str` | 思考增量 |
| `ThinkingEnd` | `signature: str \| None` | 思考结束，携带 Anthropic signature |
| `TextDelta` | `delta: str` | 正文增量 |
| `ToolUseStart` | `id: str, name: str` | 工具调用开始 |
| `ArgsDelta` | `id: str, delta: str` | 工具参数 JSON 增量 |
| `ToolUseEnd` | `id: str` | 工具调用结束 |
| `MessageEnd` | `stop_reason: StopReason, finish_reason: str \| None, truncated: bool, usage: NormalizedUsage` | 本轮结束 |

联合类型：`StreamEvent = MessageStart \| ThinkingStart \| ThinkingDelta \| ThinkingEnd \| TextDelta \| ToolUseStart \| ArgsDelta \| ToolUseEnd \| MessageEnd`

产生序列示例（DeepSeek，多 tool 调用）：

```
MessageStart {model: "deepseek-chat"}
ThinkingStart {}
ThinkingDelta {delta: "我需要先读两个文件..."}
ThinkingDelta {delta: "然后比较内容"}
ThinkingEnd   {signature: None}
ToolUseStart  {id: "call_1", name: "read_file"}
ArgsDelta     {id: "call_1", delta: '{"path"'}
ArgsDelta     {id: "call_1", delta: ': "a.txt"}'}
ToolUseEnd    {id: "call_1"}
ToolUseStart  {id: "call_2", name: "read_file"}
ArgsDelta     {id: "call_2", delta: '{"path": "b.txt"}'}
ToolUseEnd    {id: "call_2"}
MessageEnd    {stop_reason: tool_use, finish_reason: "tool_calls", truncated: false, usage: {...}}
```

### 3. Thinking 显式边界

对 Anthropic（有原生 `content_block_start` / `content_block_stop`）直接映射；对 DeepSeek（无显式边界）在 codec 内部根据 `reasoning_content` 字段出现/消失推断并插入 `ThinkingStart` / `ThinkingEnd`。上层统一通过显式边界区分。

### 4. `ThinkingBlock.signature` 字段扩展

IR 的 `ThinkingBlock` 增加 `signature: str | None = None`：

```python
@dataclass
class ThinkingBlock:
    text: str
    signature: str | None = None  # 新增
    meta: dict = field(default_factory=dict)
```

Anthropic codec 原样回传，DeepSeek codec 值为 `None`。`ThinkingEnd.signature` 与之对应。

### 5. 工具参数增量拼接

- 缓冲区位于 Loop 层（`dict[str, StringIO]`，key 为 `tool_use_id`），厂商无关
- 每次 `ArgsDelta` 追加到对应 buffer
- `ToolUseEnd` 时 buffer 完整但暂不 `json.loads`
- 收到 `MessageEnd(stop_reason=tool_use)` 后统一解析所有 buffer + 执行所有 tool
- `json.JSONDecodeError` → `ToolResultBlock(is_error=True)` 回喂模型

### 6. 错误处理

| 场景 | 处理 |
|------|------|
| 流传输中断（网络/超时） | `except Exception` 兜底 → `error(stream_disconnect)` → `run_end(llm_error)` |
| `message_end.truncated == True` | 模型侧截断，正常收尾，不重试 |
| JSON 解析失败 | `is_error=True` 回喂，模型自主决策 |

所有处理与非流式一致。重试策略正交延后（ADR-0006）。

### 7. 工具执行时机

收到 `MessageEnd(stop_reason=tool_use)` 后一次性批量执行本轮所有 tool_use。
不按 `ToolUseEnd` 逐个执行——保持冲突可串行化的基准前提（模型输出顺序 = 串行序）。

### 8. Backend Protocol 扩展

```python
class ModelBackend(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse: ...

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> Iterator[StreamEvent]: ...
```

`complete()` 保持现有语义不变（非流式聚合结果）。`stream()` 新增，返回 `Iterator[StreamEvent]`。

### 9. Loop 统一消费 + 适配器

Loop 内部私有方法 `_stream_from(backend, ...) → Iterator[StreamEvent]`：

```python
def _stream_from(self, backend, messages, tools, config):
    if hasattr(backend, "stream"):
        yield from backend.stream(messages, tools, config)
    else:
        resp = backend.complete(messages, tools, config)
        yield MessageStart(model=config.get("model", "?"))
        for block in resp.message.content:
            if isinstance(block, TextBlock):
                yield TextDelta(delta=block.text)
            elif isinstance(block, ThinkingBlock):
                yield ThinkingStart()
                yield ThinkingDelta(delta=block.text)
                yield ThinkingEnd(signature=block.signature)
            elif isinstance(block, ToolUseBlock):
                yield ToolUseStart(id=block.id, name=block.name)
                yield ArgsDelta(id=block.id, delta=_dump_json(block.input))
                yield ToolUseEnd(id=block.id)
        yield MessageEnd(
            stop_reason=resp.stop_reason,
            finish_reason=None,
            truncated=False,
            usage=resp.usage,
        )
```

Loop 核心循环始终为 `for event in self._stream_from(...)`。

### 10. Codec vs Backend 分工

Codec 层提供每流一个解码器对象，持有跨 chunk 必需的状态（thinking 边界推断、tool_call index→id 映射）：

| 层 | 文件 | 新增 | 职责 |
|----|------|------|------|
| Codec | `codecs/deepseek.py` | `DeepSeekStreamDecoder` + `decode_chunk(chunk) → list[StreamEvent]` + `flush()` | 纯翻译：OpenAI SSE chunk → 0..n 个 StreamEvent（单 chunk 可产多事件）；状态局限于单流生命周期 |
| Backend | `backend.py` | `DeepSeekBackend.stream() → Iterator[StreamEvent]` | 调用 OpenAI SDK 流式接口 + 创建解码器实例 + yield MessageStart + 循环 decode_chunk |

保持 ADR-0002 的原则：codec = 纯翻译无 SDK，backend = SDK 守门员。

### 11. StreamEvent 不携带 turn / run_id

StreamEvent 是 Backend → Loop 的中间数据，Backend 不知 turn/run_id。Loop 消费后自行 `traj.emit(...)` 注入上下文，与非流式一致。

```python
for event in self._stream_from(backend, messages, tools, config):
    if isinstance(event, MessageEnd):
        traj.emit(EventType.llm_response, turn=turn, payload={...})
```

### 12. to_dict() 与轨迹存储

所有 StreamEvent 实现 `to_dict()`，轨迹全量落盘（每轮几十到几百个 delta 事件）。存储为当前 `Event` 类型的变体，通过 `type: "stream_event"` + `payload["stream_type"]` 标识。

Golden transcript 不比对 delta 事件——只比对聚合后的 `ModelResponse.to_dict()`，与现有非流式 golden 一致。厂商格式变更时，codec 的 stream golden 测试会先炸。

### 13. Config：传输层语义分离

`ALLOWED_CONFIG_KEYS` 已有 `stream` 字段。传输层语义（stream、未来 retry/timeout 等）收敛于 `TransportConfig` dataclass，嵌套于 `AgentConfig.transport`，与业务配置（model、max_turns）分离。Loop 构造请求体时自 `config.transport.stream` 读取并写入 `config={"model": ..., "stream": flag}`。`to_public_dict()` 经 `asdict` 自动分层，轨迹 payload 里传输语义与业务配置天然分列。

### 14. Loop→CLI 实时契约（RunHandle）

`Agent.start(task, workdir) → RunHandle`，`RunHandle` 实现 `Iterator[StreamEvent]`，CLI 在 `for` 循环中实时拉取事件并渲染。`Agent.run()` 保持完全向后兼容的签名——内部排空 `start()` 后返回 `Trajectory`。

RunHandle 语义：
- `__iter__()` 驱动完整的 Agent 主循环（规约当前训练消息、LLM 调用、tool 执行、消息 append），每步 yield 当前 StreamEvent
- 主循环内的聚合、轨迹落盘与 `run()` 完全相同，只是额外 yield 事件供外界消费
- `trajectory` 属性在迭代完毕后可用，未排空时抛 `RuntimeError`
- 库用户（评测 harness）调 `run()`，零改动；CLI 调 `start()`，实时渲染

```python
class RunHandle:
    def __iter__(self) -> Iterator[StreamEvent]: ...
    @property
    def trajectory(self) -> Trajectory: ...  # 仅在迭代完毕后可访问

class Agent:
    def run(self, task, workdir) -> Trajectory:
        h = self.start(task, workdir)
        for _ in h: pass
        return h.trajectory
```

### 15. StreamEventVisitor：标准消费接口

为消除 Agent 与渲染逻辑的耦合，定义 Protocol 接口 + `isinstance` 分派函数 + no-op 基类：

```python
class StreamEventVisitor(Protocol):
    def message_start(self, ev: MessageStart) -> None: ...
    def thinking_start(self, ev: ThinkingStart) -> None: ...
    def thinking_delta(self, ev: ThinkingDelta) -> None: ...
    def thinking_end(self, ev: ThinkingEnd) -> None: ...
    def text_delta(self, ev: TextDelta) -> None: ...
    def tool_use_start(self, ev: ToolUseStart) -> None: ...
    def args_delta(self, ev: ArgsDelta) -> None: ...
    def tool_use_end(self, ev: ToolUseEnd) -> None: ...
    def message_end(self, ev: MessageEnd) -> None: ...

def dispatch_event(ev: StreamEvent, v: StreamEventVisitor) -> None: ...  # isinstance 链，与 §2 风格一致

class NullVisitor:
    def __getattr__(self, name): return lambda ev: None
```

CLI 实现 `RichStreamVisitor(NullVisitor)`；评测/调试实现 `RecordingVisitor`；ContextManager 实现 `CompressionTrigger`——全部零分支。

### 16. TransportConfig

传输层配置独立为 dataclass：

```python
@dataclass
class TransportConfig:
    stream: bool = True
    # ADR-0006 的 retry/timeout 旋钮未来进这里

@dataclass
class AgentConfig:
    ...
    transport: TransportConfig = field(default_factory=TransportConfig)
```

- stream 默认 True（CLI 默认开流式）；评测消融时显式构造 `TransportConfig(stream=False)`
- `turn_timeout_s` 暂留 AgentConfig，ADR-0006 时随 retry/timeout 一起迁入

## Consequences

- 上层所有流式消费（CLI 实时渲染、Loop 工具执行、ContextManager 压缩触发）统一通过 `StreamEvent`，零分支
- 新增厂商后端只需实现 `DeepSeekStreamDecoder`（codec 层）和 `stream()`（backend 层）
- 非流式 Backend（FakeBackend 等）零改动，Loop 内部适配器兜底
- 引入 `RunHandle` 和 `StreamEventVisitor` 两个新类型，Agent 核心对渲染零感知；`Agent.run()` 的完全向后兼容
- `TransportConfig` 将传输层语义与业务配置分离，便于消融实验独立控制
- 引入新的 `Iterator[StreamEvent]` 中间类型，增加了一层映射但消除了上层的厂商分支
- `ThinkingBlock.signature` 是 IR 的向前兼容变更，不影响现有 DeepSeek codec（默认 None）
- 完整 golden transcript 含两套：非流式的 `ModelResponse.to_dict()`（已有）+ 流式的 StreamEvent 序列（新增）

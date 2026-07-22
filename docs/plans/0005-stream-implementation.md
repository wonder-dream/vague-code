---
status: drafting
date: 2026-07-22
---

# 流式输出（StreamEvent IR）实施计划

三轮评审定稿，覆盖 ADR-0005 修订 + 5 项 codec 评审修复 + RunHandle 迭代器 + StreamEventVisitor + TransportConfig。

## 实施步骤

### 步骤1：ADR-0005 修订
- §10 措辞改为 `DeepSeekStreamDecoder`（有状态解码器，`decode_chunk(chunk) → list[StreamEvent]`）
- 新增 §14 Loop→CLI 实时契约（RunHandle 迭代器）
- 新增 §15 StreamEventVisitor
- 新增 §16 TransportConfig
- status 保持 proposed，终验时转 accepted

### 步骤2：IR 层（`src/agent/ir.py` + `tests/test_stream_ir.py`）
- `ThinkingBlock.signature: str | None`
- 9 个 StreamEvent dataclass + 联合类型 + `to_dict()`
- `StreamEventVisitor` Protocol
- `dispatch_event()` 函数
- `NullVisitor` 类
- 测试：to_dict 快照、dispatch_event 映射覆盖率、NullVisitor 不炸

### 步骤3：Codec 层（`codecs/deepseek.py` + `tests/test_deepseek_stream_codec.py`）
- `DeepSeekStreamDecoder` 类（含修订后五步流程 + flush）
- golden fixture `tests/golden/stream_*.json`（chunk 序列 + 期望事件序列）
- 评审修复 5 条全部入测试

### 步骤4：Backend 层（`backend.py`）
- `ModelBackend` Protocol 扩展 `stream()`
- `DeepSeekBackend.stream()` 实现
- `complete()` pop "stream" key
- `create_deepseek_backend` 不动

### 步骤5：Loop / Trajectory / Config（`loop.py`、`trajectory.py`、`config.py`）
- `TransportConfig` dataclass + `AgentConfig.transport` 嵌套
- `RunHandle` 类 + `Agent.start()` / `Agent.run()` 解耦
- `_stream_from` 适配器 + 聚合器 `_StreamAggregator`
- `EventType.stream_event` 枚举
- 流传输中断异常路径
- RunHandle 测试 + 消融等价性测试

### 步骤6：CLI（`cli/renderer.py` + `cli/__init__.py`）
- `RichStreamVisitor(NullVisitor)`
- `--stream/--no-stream` 参数
- `for ev in agent.start(...): dispatch_event(ev, visitor)`
- 跑后 summary 不变

## 验证命令

```powershell
pytest tests/ -q
mypy src/ tests/
ruff check src/ tests/
```

## 评审 checklist（每 commit 自审）
- [ ] 每个 if 都有 else 或显式注释说明"不可能发生"
- [ ] 所有 .get() 都有默认值或后续的空值检查
- [ ] 状态机（thinking/tool）有明确的初始态和终态，无死锁
- [ ] 同一 chunk 内多个 delta 类型共存时，输出顺序正确
- [ ] 异常路径下生成器不挂起、资源不泄漏
- [ ] 协议/接口的方法签名在实现处完全一致
- [ ] 测试用例覆盖了"评审修复"的每一条
- [ ] 无 TODO/FIXME 遗留到 commit 中

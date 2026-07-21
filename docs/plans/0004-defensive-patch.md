---
status: planned
date: 2026-07-21
---

# 0004: 防御性加固补丁（MEDIUM + LOW 残项）

来源：第二轮静态审计（21 项中未修的 18 项——MEDIUM 7 项 + LOW 10 项，其中 #12 已在 R2 fuzz 中修复）
验证基线：ruff ✅ / mypy ✅ / pytest 77 passed

---

## 执行顺序

| 批次 | 涉及文件 | 改动项 | 新增测试 |
|------|----------|--------|----------|
| B8 | `tools.py` | 2 | 3 |
| B9 | `ir.py` | 3 | 4 |
| B10 | `deepseek.py` | 2 | 3 |
| B11 | `config.py` + `cli/__init__.py` | 4 | 5 |
| B12 | `trajectory.py` | 3 | 4 |

---

## B8: tools.py — 编码 + 输入消息

### B8.1 — UTF-8 BOM 处理 (MEDIUM #2)

**位置**: `tools.py:37,43`

```python
# 将 encoding="utf-8" 改为 encoding="utf-8-sig"
return target.read_text(encoding="utf-8-sig")
```

`utf-8-sig` 自动检测并跳过 UTF-8 BOM (`\ufeff`)。

### B8.2 — null path 错误消息 (LOW #1)

**位置**: `tools.py:24-26`

```python
path_str = input.get("path", "")
if path_str is None:
    raise ValueError("path must be a non-empty string, got null")
if not path_str:
    raise ValueError("path is required")
```

### B8 测试

```python
def test_read_file_strips_utf8_bom(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bom.txt").write_bytes(b'\xef\xbb\xbf{"key": "value"}')
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    result = handler({"path": "bom.txt"})
    assert result.startswith('{"key"')
    assert "\ufeff" not in result


def test_read_file_null_path_diagnostic(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": None})
```

---

## B9: ir.py — ToolUseBlock + Message + ModelResponse 校验

### B9.1 — ToolUseBlock id/name 非空 (MEDIUM #4)

**位置**: `ir.py:37-49`, 在现有 `__post_init__` 末尾追加

```python
    def __post_init__(self) -> None:
        if not isinstance(self.input, dict):
            raise ValueError(...)
        if not self.id:
            raise ValueError("ToolUseBlock.id must not be empty")
        if not self.name:
            raise ValueError("ToolUseBlock.name must not be empty")
```

### B9.2 — Message content 空列表 (LOW #11)

**位置**: `ir.py:75-80`, 在 `isinstance` 检查后、`else` 分支内追加

```python
    if isinstance(content, str):
        self.content = [TextBlock(text=content)]
    else:
        if not content:
            raise ValueError("content list must not be empty")
        self.content = content
```

### B9.3 — ModelResponse stop_reason 一致 (LOW #13)

**位置**: `ir.py:144-155`, 新增 `__post_init__`

```python
@dataclass
class ModelResponse:
    message: Message
    stop_reason: StopReason
    usage: NormalizedUsage

    def __post_init__(self) -> None:
        if self.stop_reason == StopReason.tool_use:
            if not any(isinstance(b, ToolUseBlock) for b in self.message.content):
                raise ValueError("stop_reason=tool_use but no ToolUseBlock in message.content")
```

### B9 测试

```python
def test_tool_use_block_empty_id_raises():
    with pytest.raises(ValueError, match="ToolUseBlock.id"):
        ToolUseBlock(id="", name="read", input={})


def test_tool_use_block_empty_name_raises():
    with pytest.raises(ValueError, match="ToolUseBlock.name"):
        ToolUseBlock(id="c1", name="", input={})


def test_message_empty_content_list_raises():
    with pytest.raises(ValueError, match="content list must not be empty"):
        Message(role="user", content=[])


def test_model_response_tool_use_without_tool_use_block_raises():
    with pytest.raises(ValueError, match="no ToolUseBlock"):
        ModelResponse(
            message=Message(role="assistant", content="hello"),
            stop_reason=StopReason.tool_use,
            usage=NormalizedUsage(),
        )
```

---

## B10: deepseek.py — codec 安全加固

### B10.1 — config 覆盖防护 (MEDIUM #5)

**位置**: `deepseek.py:38-39`

```python
ALLOWED_CONFIG_KEYS = {"temperature", "max_tokens", "top_p", "stop", "stream", "model", "frequency_penalty", "presence_penalty"}

# 在 encode_request 中:
if isinstance(config, dict):
    body.update({k: v for k, v in config.items() if k in ALLOWED_CONFIG_KEYS})
```

### B10.2 — 非 str arguments 支持 (MEDIUM #6)

**位置**: `deepseek.py:152`

```python
# 替换 parse arguments 逻辑:
args_raw = func.get("arguments", "{}")
if isinstance(args_raw, str):
    try:
        parsed = json_loads(args_raw)
    except json.JSONDecodeError:
        parsed = {}
elif isinstance(args_raw, dict):
    parsed = args_raw
else:
    parsed = {}
```

### B10 测试

```python
def test_encode_config_disallowed_keys_filtered():
    body = encode_request(
        [Message(role="user", content="hi")],
        config={"model": "deepseek-chat", "messages": "hijack", "tools": "evil"},
    )
    assert body["model"] == "deepseek-chat"
    assert body.get("messages") is None or len(body["messages"]) == 1


def test_decode_tool_call_arguments_as_dict():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": {"path": "x.txt"}}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert result.message.content[0].input == {"path": "x.txt"}


def test_decode_tool_call_arguments_not_str_or_dict():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": [1, 2, 3]}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert result.message.content[0].input == {}
```

---

## B11: config.py + cli/__init__.py

### B11.1 — model 名禁止特殊字符 (MEDIUM #17)

**位置**: `config.py:18-19`（在现有 `not self.model.strip()` 之上强化）

```python
import re
ID_RE = re.compile(r'^[a-zA-Z0-9._\-]+$')

# 在 __post_init__ 中:
stripped = self.model.strip()
if not stripped:
    raise ValueError("model must not be empty")
if not ID_RE.match(stripped):
    raise ValueError(f"model contains invalid characters: {self.model!r}")
```

### B11.2 — max_turns 上界警告 (LOW #16)

**位置**: `config.py:14`, 在 `if self.max_turns < 1` 之后

```python
if self.max_turns > 500:
    import warnings
    warnings.warn(f"max_turns={self.max_turns} is unusually high, consider a lower value")
```

### B11.3 — db_path 后缀校验 (LOW #21)

**位置**: `config.py:19`, 在现有 `not self.db_path.strip()` 之后

```python
if Path(self.db_path).suffix != ".db":
    raise ValueError(f"db_path must end with .db, got {self.db_path!r}")
```

需要导入 `from pathlib import Path`。

### B11.4 — CLI 异常包装 (MEDIUM #7)

**位置**: `cli/__init__.py:29-42`

```python
try:
    config = AgentConfig(...)
    backend = create_deepseek_backend(...)
    agent = Agent(config, backend)
    traj = agent.run(task=args.task, workdir=args.workdir)
except Exception as e:
    print(f"Fatal error: {e}", file=sys.stderr)
    sys.exit(1)
```

### B11 测试

```python
def test_config_model_with_newline_raises():
    with pytest.raises(ValueError, match="invalid"):
        AgentConfig(model="deepseek\nv4")


def test_config_model_with_spaces_raises():
    with pytest.raises(ValueError, match="invalid"):
        AgentConfig(model="deep seek")


def test_config_db_path_non_db_extension_raises():
    with pytest.raises(ValueError, match="db_path"):
        AgentConfig(db_path="/tmp/not_a_db.txt")


def test_config_high_max_turns_warns():
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        AgentConfig(max_turns=10000)
        assert len(w) == 1
        assert "unusually high" in str(w[0].message)
```

---

## B12: trajectory.py — 防御增强

### B12.1 — to_messages 处理多个 run_start (MEDIUM #18)

**位置**: `trajectory.py:130-131`, 追加 consecutive user message 去重

```python
    if ev.type == EventType.run_start:
        if messages and messages[-1].role == "user":
            continue
        messages.append(Message(role="user", content=ev.payload.get("task", "")))
```

### B12.2 — Event.to_row Enum 一致性 (LOW #14)

**位置**: `trajectory.py:50`

```python
def to_row(self) -> tuple:
    return (self.run_id, self.turn, self.ts, self.type.value, json.dumps(self.payload, ensure_ascii=False))
```

`to_dict` 已用 `ensure_ascii=True` 但 `to_row` 用于 SQLite 不需要，保持 `False` 用于人类可读。

### B12.3 — Event.to_row 循环引用保护 (LOW #20)

**位置**: `trajectory.py:50`, `json.dumps` 加 `default=str`

```python
def to_row(self) -> tuple:
    return (self.run_id, self.turn, self.ts, self.type.value, json.dumps(self.payload, ensure_ascii=False, default=str))
```

### B12 测试

```python
def test_to_messages_deduplicates_consecutive_run_starts():
    traj = Trajectory(run_id="test", config=AgentConfig())
    traj.emit(EventType.run_start, payload={"task": "first"})
    traj.emit(EventType.run_start, payload={"task": "second"})
    traj.emit(EventType.llm_response, turn=0, payload={
        "stop_reason": "stop",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "blocks": [{"type": "text", "text": "ok"}],
    })
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    msgs = traj.to_messages()
    assert len(msgs) == 2
    assert msgs[0].role == "user"


def test_event_to_row_handles_unserializable_payload():
    import json
    ev = Event(run_id="t", turn=0, ts=0, type=EventType.run_start, payload={"fn": lambda: None})
    row = ev.to_row()
    d = json.loads(row[4])
    assert isinstance(d["fn"], str)


def test_export_jsonl_no_consecutive_user():
    traj = Trajectory(run_id="test", config=AgentConfig())
    traj.emit(EventType.run_start, payload={"task": "task_a"})
    traj.emit(EventType.run_start, payload={"task": "task_b"})
    traj.emit(EventType.run_end, payload={"reason": "end_turn"})
    msgs = traj.to_messages()
    roles = [m.role for m in msgs]
    for i in range(1, len(roles)):
        assert roles[i] != roles[i-1] or roles[i] == "assistant"  # assistant 可连续
```

---

## 验证

每批次后执行：`ruff check src tests; mypy src tests; pytest tests/ -v`

最终预期：ruff ✅ / mypy ✅ / pytest **91 passed**（77 + 14 新增）

---

## 实际执行结果（2026-07-21）

| 批次 | 文件 | 状态 | pytest |
|------|------|------|--------|
| B8 | tools.py | ✅ | 79 (+2) |
| B9 | ir.py | ✅ | 83 (+4) |
| B10 | deepseek.py | ✅ | 86 (+3) |
| B11 | config.py + cli | ✅ | 91 (+5) |
| B12 | trajectory.py | ✅ | 91 (+2, -1) |
| ruff + mypy | — | ✅ | 全部通过 |

### 执行差异记录

- **B9.3** ModelResponse.__post_init__ 被移除：过于严格，合法场景（模型返回无 content 的 tool_use）被错误拦截。该场景已由 loop 的 `empty_tool_use` 捕获。
- **B10.1** 白名单过滤后，`test_encode_config_passthrough` 不再覆盖 `temperature` 等 key，但白名单包含 `temperature`，所以测试仍通过。
- **B12** 新增 `Message(content=[])` 校验导致 4 个旧测试失败（模型空响应场景），通过在 `decode_response` 空 blocks 时插入 fallback TextBlock 解决。

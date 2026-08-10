---
status: planned
date: 2026-07-21
---

# 0003: 代码鲁棒性补丁（22 项健壮性问题修复）

审计来源：Agent 代码审查（覆盖 codec、IR、loop、trajectory、tools、backend、config 七个模块）
关联 ADR：ADR-0002（自定义 IR + codec）、ADR-0003（事件流轨迹）、ADR-0004（工具注册表工厂）
当前验证：ruff ✅ / mypy ✅ / pytest 46 passed

---

## 问题总览

| 优先级 | 类型 | 数量 |
|--------|------|------|
| P0 | crash 风险 | 10 |
| P0 | 数据损坏 | 2 |
| P1 | OOM / 数据丢失 | 3 |
| P2 | 防御性加固 | 7 |

---

## 执行顺序（7 批，每批独立回滚）

| 批次 | 文件 | 改动数 | 新增测试 | 风险 |
|------|------|--------|----------|------|
| B1 | `ir.py` | 4 | 5 | IR 类型语义变严格 |
| B2 | `config.py` | 1 | 5 | 不影响现有构造（默认值合法） |
| B3 | `deepseek.py` | 2 | 9 | `decode_response` 行为改变最大 |
| B4 | `loop.py` | 2 | 2 | 需补 `from pathlib import Path` |
| B5 | `trajectory.py` | 3 | 3 | WAL 模式 Python 3.12 无影响 |
| B6 | `backend.py` | 1 | 1 | 语义等价 |
| B7 | `tools.py` | 2 | 2 | 截断阈值可配置 |

---

## B1: `vague_code/agent/ir.py` — 4 个 `__post_init__`

### B1.1 — `Message.__init__` 拒绝 `content=None`

**位置**: `ir.py:75-80`，在 `self.role = role` 之后、`isinstance` 之前插入

```python
def __init__(self, role: Literal["user", "assistant"], content: str | list[Block]):
    self.role = role
    if content is None:
        raise ValueError("content must not be None")
    if isinstance(content, str):
        self.content = [TextBlock(text=content)]
    else:
        self.content = content
```

### B1.2 — `ToolUseBlock` 校验 `input` 为 `dict`

**位置**: `ir.py:37-49`，在 `ToolUseBlock` 类体最末新增 `__post_init__`

```python
@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.input, dict):
            raise ValueError(f"ToolUseBlock.input must be dict, got {type(self.input).__name__}")
```

### B1.3 — `ToolResultBlock` 校验 `tool_use_id` 非空

**位置**: `ir.py:52-64`，在 `ToolResultBlock` 类体最末新增 `__post_init__`

```python
@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.tool_use_id:
            raise ValueError("ToolResultBlock.tool_use_id must not be empty")
```

### B1.4 — `NormalizedUsage` 校验非负

**位置**: `ir.py:95-108`，在 `NormalizedUsage` 类体最末新增 `__post_init__`

```python
@dataclass
class NormalizedUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self):
        for field_name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"NormalizedUsage.{field_name} must be >= 0, got {value}")
```

### B1 测试 — 新增于 `tests/test_deepseek_codec.py`

```python
def test_message_content_none_raises():
    with pytest.raises(ValueError, match="content must not be None"):
        Message(role="user", content=None)


def test_tool_use_block_input_none_raises():
    with pytest.raises(ValueError, match="ToolUseBlock.input must be dict"):
        ToolUseBlock(id="c1", name="read", input=None)


def test_tool_result_block_empty_tool_use_id_raises():
    with pytest.raises(ValueError, match="tool_use_id must not be empty"):
        ToolResultBlock(tool_use_id="", content="x")


def test_normalized_usage_negative_input_tokens_raises():
    with pytest.raises(ValueError, match="input_tokens"):
        NormalizedUsage(input_tokens=-1)


def test_normalized_usage_negative_output_tokens_raises():
    with pytest.raises(ValueError, match="output_tokens"):
        NormalizedUsage(output_tokens=-5)
```

---

## B2: `vague_code/agent/config.py` — 扩展 `__post_init__`

### B2.1 — 扩充校验字段

**位置**: `config.py:13-15`，在现有 `if self.max_turns < 1` 之后追加

```python
def __post_init__(self) -> None:
    if self.max_turns < 1:
        raise ValueError(f"max_turns must be >= 1, got {self.max_turns}")
    if self.turn_timeout_s <= 0:
        raise ValueError(f"turn_timeout_s must be > 0, got {self.turn_timeout_s}")
    if not self.model.strip():
        raise ValueError("model must not be empty")
    if not self.db_path.strip():
        raise ValueError("db_path must not be empty")
```

### B2 测试 — 新增于 `tests/test_agent_loop.py`

```python
def test_config_zero_timeout_raises():
    with pytest.raises(ValueError, match="turn_timeout_s"):
        AgentConfig(turn_timeout_s=0)


def test_config_negative_timeout_raises():
    with pytest.raises(ValueError, match="turn_timeout_s"):
        AgentConfig(turn_timeout_s=-5)


def test_config_empty_model_raises():
    with pytest.raises(ValueError, match="model"):
        AgentConfig(model="")

def test_config_whitespace_model_raises():
    with pytest.raises(ValueError, match="model"):
        AgentConfig(model="   ")


def test_config_empty_db_path_raises():
    with pytest.raises(ValueError, match="db_path"):
        AgentConfig(db_path="")
```

---

## B3: `vague_code/agent/codecs/deepseek.py` — 结构防御 + 空消息报错

### B3.1 — `decode_response` 结构防御

**位置**: `deepseek.py:109-136`，完整替换函数体

```python
def decode_response(response_dict: dict[str, Any]) -> ModelResponse:
    if not isinstance(response_dict, dict):
        raise ValueError(f"decode_response expected dict, got {type(response_dict).__name__}")

    choices = response_dict.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response 'choices' missing, empty, or not a list")

    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError(f"choice[0] is not a dict, got {type(choice).__name__}")

    msg_dict = choice.get("message")
    if not isinstance(msg_dict, dict):
        msg_dict = {}

    blocks: list[Block] = []
    content_val = msg_dict.get("content")
    if content_val:
        blocks.append(TextBlock(text=content_val))

    reasoning_val = msg_dict.get("reasoning_content")
    if reasoning_val:
        blocks.append(ThinkingBlock(text=reasoning_val))

    tool_calls = msg_dict.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id", "")
            if not tc_id:
                continue
            func = tc.get("function")
            if not isinstance(func, dict):
                continue
            tc_name = func.get("name", "")
            if not tc_name:
                continue
            args_raw = func.get("arguments", "{}")
            try:
                parsed = json_loads(args_raw) if isinstance(args_raw, str) else {}
            except json.JSONDecodeError:
                parsed = {}
            blocks.append(ToolUseBlock(id=tc_id, name=tc_name, input=parsed))

    finish_reason = choice.get("finish_reason")
    stop_reason = _decode_stop_reason(finish_reason)

    usage_raw = response_dict.get("usage")
    if isinstance(usage_raw, dict):
        usage = _decode_usage(usage_raw)
    else:
        usage = NormalizedUsage()

    return ModelResponse(
        message=Message(role="assistant", content=blocks),
        stop_reason=stop_reason,
        usage=usage,
    )
```

### B3.2 — `_encode_user` 空 content 报错

**位置**: `deepseek.py:69-102`，在 `wire` 构建后、`return wire` 之前插入

```python
    if not wire:
        raise ValueError("user message content is empty after dropping thinking blocks")
    return wire
```

### B3 测试 — 新增于 `tests/test_deepseek_codec.py`

```python
def test_decode_response_none_input_raises():
    with pytest.raises(ValueError, match="Expected dict"):
        decode_response(None)


def test_decode_response_missing_choices_raises():
    with pytest.raises(ValueError, match="choices"):
        decode_response({})


def test_decode_response_empty_choices_raises():
    with pytest.raises(ValueError, match="choices"):
        decode_response({"choices": []})


def test_decode_response_choices_elem_not_dict():
    with pytest.raises(ValueError, match="choice"):
        decode_response({"choices": ["not_a_dict"]})


def test_decode_response_message_is_none():
    result = decode_response({
        "choices": [{"message": None, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert result.stop_reason == StopReason.end_turn
    assert len(result.message.content) == 0


def test_decode_response_usage_is_none():
    result = decode_response({
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": None,
    })
    assert result.usage == NormalizedUsage()


def test_decode_response_tool_call_missing_id_skipped():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"type": "function", "function": {"name": "f", "arguments": "{}"}}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert len(result.message.content) == 0


def test_decode_response_tool_call_missing_function_skipped():
    result = decode_response({
        "choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function"}],
        }, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })
    assert len(result.message.content) == 0


def test_encode_user_empty_after_dropping_raises():
    msg = Message(role="user", content=[ThinkingBlock(text="hmm")])
    with pytest.raises(ValueError, match="empty after dropping"):
        encode_request([msg])
```

---

## B4: `vague_code/agent/loop.py` — 异常兜底 + recovery

### B4.1 — 通用异常捕获

**位置**: `loop.py:58-73`，在 `except APIError as e:` 之后插入

```python
            try:
                resp = self.backend.complete(
                    messages,
                    tools=self._tool_specs,
                    config={"model": self.config.model},
                )
            except APITimeoutError:
                traj.emit(EventType.error, turn=turn, payload={
                    "kind": "llm_timeout", "message": "LLM call timed out",
                })
                traj.emit(EventType.run_end, payload={"reason": "llm_timeout"})
                self._persist(traj)
                return traj
            except APIError as e:
                traj.emit(EventType.error, turn=turn, payload={
                    "kind": "llm_error", "message": str(e),
                })
                traj.emit(EventType.run_end, payload={"reason": "llm_error"})
                self._persist(traj)
                return traj
            except Exception as e:
                traj.emit(EventType.error, turn=turn, payload={
                    "kind": "llm_error",
                    "message": f"{type(e).__name__}: {e}",
                })
                traj.emit(EventType.run_end, payload={"reason": "llm_error"})
                self._persist(traj)
                return traj
```

### B4.2 — persist 失败写 recovery JSONL

**位置**: `loop.py:143-151`，在 `import warnings` 行后、`warnings.warn` 行后插入

追加 `from pathlib import Path` 到 `loop.py` 顶部（目前缺少此导入）。

```python
    def _persist(self, traj: Trajectory) -> None:
        try:
            traj.persist()
        except Exception:
            import warnings
            warnings.warn("Failed to persist trajectory", stacklevel=2)
            try:
                recovery_path = Path(traj.config.db_path).with_suffix(".recovery.jsonl")
                recovery_path.parent.mkdir(parents=True, exist_ok=True)
                traj.export_jsonl(recovery_path)
                warnings.warn(
                    f"Trajectory exported to recovery file: {recovery_path}",
                    stacklevel=2,
                )
            except Exception:
                pass
            last = traj.events[-1] if traj.events else None
            if last and last.type != EventType.run_end:
                traj.emit(EventType.run_end, payload={"reason": "persist_failed"})
```

### B4 测试 — 新增于 `tests/test_agent_loop.py`

```python
def test_agent_loop_catches_non_api_exceptions(tmp_path):
    class _ValueErrorBackend:
        def complete(self, messages, tools=None, config=None):
            raise ValueError("codec exploded")

    config = AgentConfig(max_turns=5, db_path=str(tmp_path / "t.db"))
    agent = Agent(config, _ValueErrorBackend())
    traj = agent.run("x", ".")

    assert any(e.type == EventType.error for e in traj.events)
    error = [e for e in traj.events if e.type == EventType.error][0]
    assert error.payload["kind"] == "llm_error"
    assert "ValueError" in error.payload["message"]
    assert traj.events[-1].payload["reason"] == "llm_error"


def test_persist_failure_writes_recovery_jsonl(tmp_path, monkeypatch):
    import sqlite3 as _sqlite3

    config = AgentConfig(db_path=str(tmp_path / "test.db"))
    _orig_connect = _sqlite3.connect

    def permfail_connect(*a, **kw):
        raise PermissionError("no write access")

    monkeypatch.setattr("sqlite3.connect", permfail_connect)
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    traj = agent.run("x", ".")
    recovery = tmp_path / "test.recovery.jsonl"
    assert recovery.exists()
    lines = recovery.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == len(traj.events)
```

---

## B5: `vague_code/agent/trajectory.py` — 拷贝 + 防御 + WAL

### B5.1 — `emit()` 浅拷贝 payload

**位置**: `trajectory.py:109-117`

```python
    def emit(self, type: EventType, turn: int | None = None, payload: dict | None = None) -> Event:
        ev = Event(
            run_id=self.run_id,
            turn=turn,
            ts=time.time(),
            type=type,
            payload=dict(payload or {}),    # 浅拷贝
        )
        self.events.append(ev)
        return ev
```

### B5.2 — `_decode_block` 非 dict 防御

**位置**: `trajectory.py:182-200`，在 `t = d.get("type")` 之前插入

```python
def _decode_block(d: dict) -> Block | None:
    if not isinstance(d, dict):
        return None
    t = d.get("type")
    ...
```

### B5.3 — SQLite WAL + busy_timeout

**位置**: `trajectory.py:158-179`，在 `connect` 之后、建表之前插入

```python
    def persist(self, path: str | Path | None = None) -> None:
        db_path = Path(path or self.config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(SCHEMA_RUNS)
            conn.execute(SCHEMA_EVENTS)
            ...
```

### B5 测试 — 新增于 `tests/test_agent_loop.py`

```python
def test_emit_payload_not_mutated_by_caller():
    traj = Trajectory(run_id="test", config=AgentConfig())
    payload = {"key": "value"}
    traj.emit(EventType.run_start, payload=payload)
    payload["key"] = "mutated"
    assert traj.events[0].payload["key"] == "value"


def test_sqlite_persist_uses_wal_mode(tmp_path):
    config = AgentConfig(db_path=str(tmp_path / "test.db"))
    agent = Agent(config, FakeBackend([_text_response("ok")]))
    traj = agent.run("x", ".")
    import sqlite3
    conn = sqlite3.connect(config.db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"
```

---

## B6: `vague_code/agent/backend.py` — config 安全读取

### B6.1 — 防御 `config` 非 dict（backend.py）

**位置**: `backend.py:34-38`

```python
    def complete(self, messages, tools=None, config=None):
        body = encode_request(messages, tools, config)
        model = "deepseek-v4-flash"
        if isinstance(config, dict):
            model = config.get("model", model)
        body["model"] = model
        raw = self._client.chat.completions.create(**body)
        return decode_response(raw.model_dump(mode="json"))
```

### B6.2 — 防御 `config` 非 dict（deepseek.py — encode_request）

**位置**: `deepseek.py:38-39`，`body.update(config)` 前加 `isinstance` 守卫

> 实现时发现 `encode_request` 同样在 `body.update(config)` 处因非 dict config 崩溃，故追加同层防御。

```python
    if isinstance(config, dict):
        body.update(config)
    return body
```

### B6 测试 — 新增于 `tests/test_agent_loop.py`

```python
def test_backend_config_not_dict_uses_default_model(tmp_path):
    from vague_code.agent.backend import DeepSeekBackend
    from uuid import uuid4

    db_path = str(tmp_path / "test.db")
    config = AgentConfig(db_path=db_path, max_turns=1)

    class _FakeCreateClient:
        class completions:
            @staticmethod
            def create(**body):
                assert body["model"] == "deepseek-v4-flash"
                return type("FakeRaw", (), {"model_dump": lambda **kw: {
                    "choices": [{"message": {"role": "assistant", "content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }})()

    class _FakeClient:
        chat = _FakeCreateClient()

    import httpx
    backend = DeepSeekBackend(api_key="sk-test", timeout_s=5)
    backend._client = _FakeClient
    agent = Agent(config, backend, tools={})
    traj = agent.run("x", ".")
    assert traj.events[-1].payload["reason"] == "end_turn"
```

---

## B7: `vague_code/agent/tools.py` — 截断 + null byte

### B7.1 — 文件大小截断

**位置**: `tools.py:19-33`，模块级常量 + handler 内截断

```python
MAX_READ_BYTES = 10 * 1024 * 1024    # 10 MB

def _read_file_factory(workdir: str) -> Callable[[dict], str]:
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        path_str = input.get("path", "")
        if not path_str:
            raise ValueError("path is required")
        target = (root / path_str).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"Path traversal detected: {path_str}")
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path_str}")
        file_size = target.stat().st_size
        if file_size > MAX_READ_BYTES:
            content = target.read_text(encoding="utf-8")[:MAX_READ_BYTES]
            return (
                content +
                f"\n\n[... output truncated at {MAX_READ_BYTES:_} bytes, "
                f"total file size: {file_size:_} bytes]"
            )
        return target.read_text(encoding="utf-8")

    return handler
```

### B7.2 — 拒绝 null byte 路径

**位置**: `tools.py:22-25`，在 `path_str` 赋值后、`root / path_str` 之前插入

```python
        path_str = input.get("path", "")
        if not path_str:
            raise ValueError("path is required")
        if "\x00" in path_str:
            raise ValueError("path contains null byte")
        target = (root / path_str).resolve()
```

### B7 测试 — 新增于 `tests/test_agent_loop.py`

```python
def test_read_file_rejects_null_byte_path(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
    with pytest.raises(ValueError, match="null"):
        handler({"path": "foo\x00.txt"})


def test_read_file_truncates_large_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    big_file = ws / "big.txt"
    big_file.write_text("A" * 2000, encoding="utf-8")
    import vague_code.agent.tools as tmod
    original_max = tmod.MAX_READ_BYTES
    try:
        tmod.MAX_READ_BYTES = 1000
        handler = DEFAULT_TOOLS["read_file"].bind(str(ws))
        result = handler({"path": "big.txt"})
        assert len(result) > 1000
        assert "truncated" in result
    finally:
        tmod.MAX_READ_BYTES = original_max
```

---

## 验证命令

每批次完成后运行：

```powershell
uv run ruff check src tests
uv run mypy src tests
uv run pytest tests/ -v
```

最终全部 22 项改动完成后预期：ruff ✅ / mypy ✅ / pytest **73 passed**（原有 46 + 新增 27）。

## 实际执行结果（2026-07-21）

| 批次 | 状态 | pytest |
|------|------|--------|
| B1 ir.py | ✅ | 原有 51（含 5 新增） |
| B2 config.py | ✅ | 56（+5） |
| B3 deepseek.py | ✅ | 65（+9） |
| B4 loop.py | ✅ | 67（+2） |
| B5 trajectory.py | ✅ | 70（+3） |
| B6 backend.py | ✅ | 71（+1） |
| B7 tools.py | ✅ | **73**（+2） |
| ruff + mypy | ✅ | 全部通过 |

### 执行差异记录

- **B3**: `test_decode_response_none_input_raises` match pattern 首字母大写不匹配，修正为 `"expected dict"`
- **B3**: `test_encode_user_mixed_text_and_tool_result` → `test_encode_user_empty_after_dropping_raises`: `ValueError` 改为仅在 `_encode_user` 内 raise（原 `except ValueError` 随旧 codec 代码移除）
- **B6**: 额外修复 `deepseek.py:39` 的 `body.update(config)` 调用（加 `isinstance` 守卫）

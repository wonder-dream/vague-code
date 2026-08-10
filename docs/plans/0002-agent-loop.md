# Agent Loop 主循环（修订版，对应 day0 → day1）

## 目标

将 `day0_minimal_loop.py` 的裸 while 循环迁移为符合 ADR-0001 的库形态 `Agent(config, backend).run(task, workdir) → Trajectory`。复用 day1 的 IR (`vague_code/agent/ir.py`) 与 DeepSeek codec (`vague_code/agent/codecs/deepseek.py`)，带轮次熔断、SQLite 事件流轨迹、异常处理和 API key 脱敏。Agent 内部不接触任何厂商协议类型，仅与 IR 交互。

## 改动清单

### 1. `pyproject.toml` 加依赖

```toml
dependencies = [
    ...,
    "python-dotenv>=1.1.0",
]
```

### 2. `vague_code/agent/config.py`（新建）

`AgentConfig` dataclass — 厂商无关，不含 api_key/base_url：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | `str` | `"deepseek-v4-flash"` | |
| `max_turns` | `int` | `20` | 最大轮次，达到即熔断 |
| `turn_timeout_s` | `float` | `120.0` | 单轮 LLM 调用超时，透传到 backend HTTP timeout |
| `db_path` | `str` | `"runs/runs.db"` | SQLite 数据库路径 |

`to_public_dict()`：序列化时脱敏（当前无敏感字段，保留为防回归钩子）。

### 3. `vague_code/agent/backend.py`（新建）

`ModelBackend` 协议：

```python
class ModelBackend(Protocol):
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        config: dict | None = None,
    ) -> ModelResponse: ...
```

`DeepSeekBackend` 实现（放 `vague_code/agent/backend.py`；`codecs/deepseek.py` 只保留纯函数）：

- 持有 `OpenAI(api_key=..., base_url=..., timeout=turn_timeout_s)` 实例
- `complete()` 调用 codec 的 `encode_request` → `client.chat.completions.create` → `decode_response`
- timeout 落地为 OpenAI client 的 HTTP 超时参数

```python
class DeepSeekBackend:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 timeout_s: float = 120.0):
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)

    def complete(self, messages, tools=None, config=None) -> ModelResponse:
        body = encode_request(messages, tools, config)
        body["model"] = (config or {}).get("model", "deepseek-v4-flash")
        raw = self._client.chat.completions.create(**body)
        return decode_response(raw.model_dump(mode="json"))
```

`create_deepseek_backend(api_key, base_url, timeout_s) -> DeepSeekBackend` 工厂函数。

### 4. `vague_code/agent/trajectory.py`（新建）

按 ADR-0003 事件流规范，SQLite 为主，JSONL 为调试导出。

#### SQLite schema

```sql
runs(
    run_id      TEXT PRIMARY KEY,
    task        TEXT,
    workdir     TEXT,
    config_json TEXT,
    status      TEXT,
    created_at  REAL
)
events(
    run_id TEXT,
    turn   INTEGER,   -- 可为 NULL（run_start/run_end）
    ts     REAL,
    type   TEXT,
    payload TEXT      -- JSON 格式
)
```

#### 事件类型（公共字段：run_id, turn, ts, type；payload 额外字段）

| 事件 | turn | payload 必要字段 | 说明 |
|------|------|----------|------|
| `run_start` | NULL | `task`, `workdir`, `config`（已脱敏） | |
| `turn_start` | n | — | |
| `llm_response` | n | `stop_reason`, `usage`, `blocks` | |
| `tool_call` | n | `id`, `name`, `input` | |
| `tool_result` | n | `tool_use_id`, `content`, `is_error` | |
| `error` | n| `kind`, `message` | kind ∈ llm_timeout / llm_error / tool_bind_error / empty_tool_use |
| `run_end` | NULL | `reason` | reason ∈ end_turn / max_turns / max_tokens / content_filter / unknown / llm_error / llm_timeout / tool_bind_error / empty_tool_use |

#### API

- `Trajectory(run_id, config)` — 构造，初始化事件列表
- `.emit(type, turn, payload)` — 添加事件（自动填 run_id + ts）
- `.to_messages() -> list[Message]` — 从事件流导出 IR messages（纯函数）。喂 LLM-as-Judge（OpenAI 格式）时复用 codec 的 `encode_request` 投影即可，无需另写导出器。
- `.export_jsonl(path)` — 调试导出 JSONL（非主要存储）
- `.persist(path)` — 写入 SQLite（含 runs 表 upsert + events 表批量插入）

### 5. `vague_code/agent/loop.py`（新建）

`Agent.__init__(config, backend)` — 接受 ModelBackend 实现，不接触厂商类型。

`Agent.run(task, workdir) → Trajectory`：

```
1. run_id = uuid4().hex[:12]
2. 构造 Trajectory(run_id, config), emit run_start
3. messages = [Message(role="user", content=task)]
4. turn = 0
5. while turn < max_turns:
   a. emit turn_start(turn)
   b. try:
        resp = backend.complete(messages, tools, {...})
      except (APITimeoutError, APIError) as e:
        emit error(kind="llm_timeout"/"llm_error", message=str(e))
        emit run_end(reason="llm_timeout"/"llm_error")
        persist trajectory, return Trajectory
   c. emit llm_response(stop_reason, usage, blocks)
   d. if stop_reason in (end_turn, stop_sequence):
        emit run_end(reason="end_turn")
        break
   e. if stop_reason in (max_tokens, content_filter, unknown):
        emit run_end(reason=stop_reason)
        break
   f. if stop_reason == tool_use:
         tool_uses = [b for b in resp.message.content if isinstance(b, ToolUseBlock)]
         if turn + 1 >= max_turns:  # 预算不足：执行完这批也轮不到下一次 LLM 调用
           emit run_end(reason="max_turns",
                        payload={"pending_tool_calls": len(tool_uses)})
           break
         messages.append(resp.message)
         tool_results: list[ToolResultBlock] = []
         for block in tool_uses:
           emit tool_call(turn, id=block.id, name=block.name, input=block.input)
           try:
             result = execute_tool(block)
             emit tool_result(tool_use_id=block.id, content=result, is_error=False)
             tool_results.append(ToolResultBlock(tool_use_id=block.id, content=result))
           except Exception as e:
             emit tool_result(tool_use_id=block.id, content=str(e), is_error=True)
             tool_results.append(ToolResultBlock(tool_use_id=block.id, content=str(e), is_error=True))
         messages.append(Message(role="user", content=tool_results))
         turn += 1
 6. trajectory.persist(db_path)
 7. return trajectory
```

#### 关于 turn 定义

1 turn = 1 次 LLM 调用 + 执行其返回的全部 tool_use。此后 turn+1 才进入下一轮循环。熔断不在 `while` 条件里检查，而在 `stop_reason == tool_use` 分支内：若当前 `turn + 1 >= max_turns`（执行完这批也轮不到下一次 LLM 调用），则工具不执行，记录 pending_tool_calls 数量后熔断。保证已发起的 LLM 调用都有机会回写事件，但不会为必死批次浪费工具执行。

### 6. 异常处理矩阵

| 故障点 | 处理 | 运行效果 |
|--------|------|----------|
| LLM 超时 / API 报错 | catch → `error` 事件 + `run_end(llm_timeout/llm_error)` | Trajectory 正常返回，用户拿到部分轨迹 |
| 工具不存在 / 参数非法 | 不执行 → `ToolResultBlock(is_error=True)` 回喂 | 模型看到错误，有机会自我修正 |
| 工具执行抛异常 | catch → 同上 | 同上 |
| 轨迹写入失败 | warning 日志，不抛；run_end 标记 persist_failed | 不影响 Agent 主流程 |
| max_tokens / content_filter / unknown | 按终止分类写入对应 run_end reason | 各理由独立记录，可观测性 |

### 7. API key 脱敏

- api_key 仅存在于 DeepSeekBackend 对象属性中，不进 AgentConfig、不进 Trajectory、不进入任何事件 payload。
- `AgentConfig.to_public_dict()` 保留为钩子，确保序列化配置时无敏感字段泄漏。
- 测试在事件流中 grep 字符串，断言无明文 key。

### 8. 事件公共字段

每事件一定携带 `run_id`、`ts`（time.time() 浮点）、`turn`（可为 NULL）、`type`、`payload`（dict 转 JSON）。确保 to_messages() 和 LLM-as-Judge 消费时能追踪到原始上下文。

### 9. `vague_code/agent/ir.py` 改动

`StopReason` 新增 `unknown`，`content_filter` 已是成员。codec 的 `_decode_stop_reason` 修正：`None`/未识别 finish_reason → `unknown`，不再复用 `stop_sequence`。

### 10. `vague_code/cli/__init__.py`（薄壳）

命令行入口：
- 解析参数（task, workdir, --model, --max-turns, --db-path, --export-jsonl, ...）
- 从 `.env` / 环境变量读取 API key（不提供 `--api-key` 参数，避免 key 泄露到 shell 历史或进程列表）
- 构造 `AgentConfig`
- 构造 `DeepSeekBackend(api_key, base_url, config.turn_timeout_s)`
- 调用 `Agent(config, backend).run(task, workdir)`
- 打印最终回复与轨迹 ID
- `--export-jsonl` 时额外写 JSONL 文件

API key 优先级：
1. `.env` 文件（`dotenv_values()` 返回 dict，不动 os.environ）
2. `DEEPSEEK_API_KEY` 环境变量

理由：.env 是用户可直接编辑的高时效配置，环境变量或 CI 中难以临时修改，故此处 .env 优先于环境变量，与主流 dotenv 惯例相反。CLI 不提供 `--api-key` 参数（shell 历史和进程列表可见问题），库调用方经 `create_deepseek_backend(api_key=...)` 显式传入。

### 11. `day0_minimal_loop.py`（删除）

### 12. 测试 `tests/test_agent_loop.py`

注入 `FakeBackend`（可编程返回队列 / 抛异常），覆盖：

**Happy path**：
- 单轮 `end_turn` → 一轮结束
- 多轮 `tool_use` → `end_turn` → 正确轮转
- `max_turns` 熔断 → `run_end(max_turns)`，pending tool_use 未执行

**边界**：
- `max_turns=1` 且首轮回 `tool_use` → 熔断，pending 计数正确
- `max_tokens` 终止 → reason 记录
- `unknown` / `content_filter` 终止 → reason 独立记录
- 空 content 响应 → 正常处理

**异常注入**：
- backend 抛超时 → `error(llm_timeout)` + `run_end(llm_timeout)`，Trajectory 正常返回
- backend 抛 API 错误 → `run_end(llm_error)`
- 工具抛异常 → `ToolResultBlock(is_error=True)` 回喂，循环继续
- 未知工具名 → 同上

**验证点**：
- 事件序列完整性（公共字段齐全、turn 递增）
- `to_messages()` 导出格式正确（含 error 事件时不会崩）
- SQLite 两表内容核对
- 事件流中无明文 api_key
- JSONL 导出格式验证

### 13. 验证

```bash
ruff check src tests
mypy src tests
pytest tests/ -v
```

## 不做（留后续迭代）

- 流式输出、指数退避重试、checkpoint 恢复（5.1 节已安排但本次范围外）
- 压缩流水线、权限系统
- 工具并发调度
- 多工具类型（本次仅 read_file）



Reviewed by 我，2026-07-21，结论：通过

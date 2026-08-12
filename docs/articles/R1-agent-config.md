# R1：AgentConfig 参考

**谁需要读：** 使用 `Agent(config).run()` 或 CLI 参数的开发者
**前置阅读：** 04-agent-runtime.md
**读完能做什么：** 精确控制 Agent 的每个配置项

---

## 1. AgentConfig 字段表

**代码位置：** `config.py:62-71`

| 字段 | 类型 | 默认值 | 约束 | CLI 参数 | 说明 |
|------|------|--------|------|----------|------|
| model | str | `"deepseek-v4-flash"` | 非空，仅含 `[\w.\-]` | `--model` | 模型名称，需在 CONTEXT_WINDOWS 中注册 |
| max_turns | int | 20 | 1 ≤ n ≤ 500（>500 警告） | `--max-turns` | 最大对话轮次 |
| db_path | str | `"runs/runs.db"` | 以 `.db` 或 `.sqlite` 结尾 | `--db-path` | SQLite 轨迹数据库路径 |
| transport | TransportConfig | TransportConfig() | — | 见下表 | 传输层配置 |
| compression | CompressionConfig | CompressionConfig() | — | — | 五层压缩配置 |
| concurrent_tools | bool | `False` | — | — | 启用工具并发调度 |
| permission_mode | str | `"normal"` | `"safe"`/`"normal"`/`"autoedit"`/`"auto"` | `/mode` | 权限模式 |
| memory | MemoryConfig | MemoryConfig() | — | — | 记忆系统配置 |

---

## 2. TransportConfig 字段表

**代码位置：** `config.py:9-26`

| 字段 | 类型 | 默认值 | 约束 | CLI 参数 | 说明 |
|------|------|--------|------|----------|------|
| stream | bool | `True` | — | `--stream`/`--no-stream` | 流式输出 |
| timeout_s | float | 120.0 | > 0 | `--timeout-s` | 单轮 LLM 调用超时（秒） |
| retry_enabled | bool | `True` | — | `--retry`/`--no-retry` | 启用自动重试 |
| retry_max_attempts | int | 5 | ≥ 0 | `--retry-max-attempts` | 最大重试次数（0=不重试） |
| retry_base_s | float | 2.0 | > 0 | `--retry-base-s` | 指数退避基数（秒） |
| retry_max_delay_s | float | 120.0 | > 0 | `--retry-max-delay-s` | 最大重试间隔（秒） |

---

## 3. CompressionConfig 字段表

**代码位置：** `config.py:28-50`

| 字段 | 类型 | 默认值 | 约束 | 说明 |
|------|------|--------|------|------|
| enabled | bool | `True` | — | 启用压缩（false=直通） |
| microcompact_threshold | float | 0.5 | 0 ≤ n ≤ 1 | 触发 microcompact 的利用率阈值 |
| microcompact_max_chars | int | 4000 | ≥ 1 | ToolResultBlock 最大长度，超出则折叠 |
| microcompact_keep_recent | int | 3 | ≥ 0 | microcompact 豁免的最近消息对数 |
| structured_snip_threshold | float | 0.65 | 0 ≤ n ≤ 1 | 触发 structured_snip 的利用率阈值 |
| structured_snip_keep_recent | int | 3 | ≥ 0 | structured_snip 豁免的最近闭合子任务数 |
| auto_compact_threshold | float | 0.85 | 0 ≤ n ≤ 1 | 触发 auto_compact 的利用率阈值 |
| auto_compact_keep_turns | int | 4 | ≥ 0 | auto_compact 保留的最近轮次 |
| stale_snip_keep_recent | int | 3 | ≥ 0 | stale_snip 豁免的最近结果数 |

---

## 4. MemoryConfig 字段表

**代码位置：** `config.py`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | `True` | 启用记忆系统 |
| memory_file | str | `".agent/memory.md"` | 记忆文件路径（相对 workdir 解析，绝对路径直用） |
| session_end_distill | bool | `True` | 会话结束是否执行 LLM 总结蒸馏 |
| distill_model | str \| None | `None` | 蒸馏模型（None = 主 agent 模型） |

---

## 5. RepoMapConfig 字段表

**代码位置：** `config.py:61-66`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | `True` | 启用 repo map 符号索引 |
| max_map_tokens | int | 1000 | 注入 system prompt 的符号地图 token 上限 |
| max_files | int | 2000 | 索引的最大文件数 |
| languages | list[str] | `["python"]` | 索引的语言 |

---

## 6. 编程用法示例

```python
from vague_code.agent.config import AgentConfig, TransportConfig, CompressionConfig, MemoryConfig
from vague_code.agent.loop import Agent
from vague_code.agent.backend import create_deepseek_backend

config = AgentConfig(
    model="deepseek-v4-flash",
    max_turns=30,
    db_path="runs/runs.db",
    transport=TransportConfig(stream=True, timeout_s=120.0),
    compression=CompressionConfig(enabled=True),
    concurrent_tools=True,
    permission_mode="autoedit",
    memory=MemoryConfig(enabled=True),
)

backend = create_deepseek_backend(api_key="sk-xxx")
agent = Agent(config, backend)
traj = agent.run("Fix the bug", "/path/to/project")
print(f"Run {traj.run_id} finished")
```

---

## 7. CLI ↔ Config 映射表

| CLI 参数 | config 字段 | 转换 |
|----------|-----------|------|
| `--model` | `config.model` | 直接赋值 |
| `--max-turns` | `config.max_turns` | `int()` |
| `--db-path` | `config.db_path` | 直接赋值 |
| `--stream`/`--no-stream` | `config.transport.stream` | bool |
| `--timeout-s` | `config.transport.timeout_s` | `float()` |
| `--retry`/`--no-retry` | `config.transport.retry_enabled` | bool |
| `--retry-max-attempts` | `config.transport.retry_max_attempts` | `int()` |
| `--retry-base-s` | `config.transport.retry_base_s` | `float()` |
| `--retry-max-delay-s` | `config.transport.retry_max_delay_s` | `float()` |
| `--provider` | backend 选择 | `"deepseek"`→DeepSeekBackend，`"anthropic"`→AnthropicBackend |
| `--permission-mode` | `config.permission_mode` | 直接赋值 |
| `--no-repo-map` | `config.repo_map.enabled` | `not args.no_repo_map` |
| `--repo-map-tokens` | `config.repo_map.max_map_tokens` | `int()` |

---

## 下一篇

→ **R2：IR 参考**——Block、Message、ModelResponse、StopReason、StreamEvent 的完整 API 说明。

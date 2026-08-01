# 细纲：agent-config.md

**预估行数：** ~200 行（表格风格）
**定位：** API 参考——配置项。

---

## 开头

- **谁需要读：** 使用 `Agent(config).run()` 或 CLI 参数的开发者
- **前置阅读：** 04-agent-runtime.md（Agent 类结构）
- **读完能做什么：** 精确控制 Agent 的每个配置项

---

## 细纲

### 1. AgentConfig 字段表

**代码位置：** `config.py:62-71`

| 字段 | 类型 | 默认值 | 约束 | CLI 参数 | 说明 |
|------|------|--------|------|----------|------|
| model | str | `"deepseek-v4-flash"` | 非空，仅含 `[\w.\-]` | `--model` | 模型名称，需在 CONTEXT_WINDOWS 中注册 |
| max_turns | int | 20 | 1 ≤ n ≤ 500（>500 警告 `config.py:77-78`） | `--max-turns` | 最大对话轮次，包含 LLM 调用 + 工具执行 |
| db_path | str | `"runs/runs.db"` | 非空，以 `.db` 或 `.sqlite` 结尾（`config.py:86-87`） | `--db-path` | SQLite 轨迹数据库路径 |
| transport | TransportConfig | TransportConfig() | — | 见下表 | 传输层配置（流式/超时/重试） |
| compression | CompressionConfig | CompressionConfig() | — | — | 五层压缩配置 |
| concurrent_tools | bool | `False` | — | — | 启用工具并发调度 |
| permission_mode | str | `"normal"` | `"safe"`/`"normal"`/`"autoedit"`/`"auto"` | `/mode`/`--permission-mode` | 权限模式 |
| repo_map | RepoMapConfig | RepoMapConfig() | — | `--no-repo-map`/`--repo-map-tokens` | repo map 符号索引配置 |
| memory | MemoryConfig | MemoryConfig() | — | — | 记忆系统配置 |

### 2. TransportConfig 字段表

**代码位置：** `config.py:9-26`

| 字段 | 类型 | 默认值 | 约束 | CLI 参数 | 说明 |
|------|------|--------|------|----------|------|
| stream | bool | `True` | — | `--stream`/`--no-stream` | 流式输出 |
| timeout_s | float | 120.0 | > 0（`config.py:19-20`） | `--timeout-s` | 单轮 LLM 调用超时（秒） |
| retry_enabled | bool | `True` | — | `--retry`/`--no-retry` | 启用自动重试 |
| retry_max_attempts | int | 5 | ≥ 0（`config.py:21-22`） | `--retry-max-attempts` | 最大重试次数（0=不重试） |
| retry_base_s | float | 2.0 | > 0（`config.py:23-24`） | `--retry-base-s` | 指数退避基数 |
| retry_max_delay_s | float | 120.0 | > 0（`config.py:25-26`） | `--retry-max-delay-s` | 最大重试间隔 |

### 3. CompressionConfig 字段表

**代码位置：** `config.py:28-50`

| 字段 | 类型 | 默认值 | 约束 | 说明 |
|------|------|--------|------|------|
| enabled | bool | `True` | — | 启用压缩（false=直通） |
| microcompact_threshold | float | 0.5 | 0 ≤ n ≤ 1（`config.py:39-40`） | 触发 microcompact 的利用率阈值 |
| microcompact_max_chars | int | 4000 | ≥ 1（`config.py:43-44`） | ToolResultBlock 最大长度，超出则折叠 |
| microcompact_keep_recent | int | 3 | ≥ 0（`config.py:45-46`） | microcompact 豁免的最近消息对数 |
| auto_compact_threshold | float | 0.85 | 0 ≤ n ≤ 1（`config.py:41-42`） | 触发 auto_compact 的利用率阈值 |
| auto_compact_keep_turns | int | 4 | ≥ 0（`config.py:49-50`） | auto_compact 保留的最近轮次 |
| stale_snip_keep_recent | int | 3 | ≥ 0（`config.py:47-48`） | stale_snip 豁免的最近结果数 |

### 4. MemoryConfig 字段表

**代码位置：** `config.py:53-59`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | `True` | 启用记忆系统（false→不读写记忆） |
| memory_db_path | str | `"runs/memory.db"` | SQLite 记忆数据库路径 |
| search_top_k | int | 5 | `memory_search` 返回的最大结果数 |
| auto_compact_distill | bool | `True` | auto_compact 摘要自动蒸馏为 episodic 记忆 |

### 5. RepoMapConfig 字段表

**代码位置：** `config.py:61-66`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | `True` | 启用 repo map 符号索引 |
| max_map_tokens | int | 1000 | 注入 system prompt 的符号地图 token 上限 |
| max_files | int | 2000 | 索引的最大文件数 |
| languages | list[str] | `["python"]` | 索引的语言 |

### 6. 编程用法示例

```python
from src.agent.config import AgentConfig, TransportConfig, CompressionConfig, MemoryConfig
from src.agent.loop import Agent
from src.agent.backend import create_deepseek_backend

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

### 7. CLI ↔ Config 映射表

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

## 结尾

**下一篇推荐：** → R2：IR 参考

---

## 本文件说明

这是文档 `agent-config.md` 的细纲。实际写作时需验证每个字段的默认值与 `config.py` 一致。

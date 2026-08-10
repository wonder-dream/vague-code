# Agent Loop 实现审查清单（v2）

实现对应计划：`docs/plans/0002-agent-loop.md`
关联 ADR：`docs/adr/0004-tool-registry-factory.md`
日期：2026-07-21
当前验证：ruff ✅ / mypy ✅ / pytest 39 passed

---

## A. 语义决策点（逐条确认是否符合预期）

- [ ] `loop.py:95-100` — 方案 B 熔断：`turn + 1 >= max_turns` 时不执行工具，记 `pending_tool_calls`
- [ ] `loop.py:58-72` — LLM 异常分类：`APITimeoutError → llm_timeout`，其余 `APIError → llm_error`，正常返回 Trajectory 不抛出
- [ ] `loop.py:81-87` — stop_reason 三类处理：`end_turn/stop_sequence` 正常；`max_tokens/content_filter/unknown` 按各自 reason 终止
- [ ] `deepseek.py:139-148` — `None`/未识别 finish_reason → `unknown`（旧行为 → `stop_sequence`，语义已变）
- [ ] `config.py:8-11` — 默认模型 `deepseek-v4-flash`，config 不含 api_key/base_url
- [ ] `backend.py:20-38,41-46` — DeepSeekBackend 放 backend.py 而非 codecs/；`create_deepseek_backend` 工厂
- [ ] `cli/__init__.py:60-66` — key 优先级 `.env > 环境变量`，`dotenv_values()` 不动 os.environ；无 `--api-key` CLI 参数
- [ ] `tools.py:19-31` — read_file 路径遍历防护（resolve 后 startswith 检查）
- [ ] `loop.py:25-33` — 注册表注入 `tools if tools is not None else DEFAULT_TOOLS`；校验 `key == spec.name`

## B. 已修复疑点（本轮关闭）

- [x] **#6 read_file 路径逃逸** — `tools.py:26-28` is_relative_to 替换 startswith，复现测试 `test_read_file_path_traversal_blocked`
- [x] **#2 to_messages() 顺序错误** — `trajectory.py:117-148` 改为单遍事件序 flush，复现测试 `test_to_messages_preserves_turn_interleaving`
- [x] **#1 export_jsonl 公共字段丢失 + 二次编码** — `trajectory.py:28-33` 新增 `Event.to_dict()`，`export_jsonl` 写完整 dict，复现测试 `test_export_jsonl_full_event_per_line`
- [x] **#3+#4 persist 非幂等 + 双 run_end 风险** — `trajectory.py:166-188` 增量 persist（`_persisted_count`）；`loop.py:143-151` B 版补发（不重复）；复现测试 `test_persist_is_idempotent` + `test_persist_failure_does_not_duplicate_run_end`
- [x] **#5 api_key 泄漏测试无效** — 替换为 `test_agent_config_has_no_sensitive_fields` 结构断言

## C. 常规核对

- [ ] `ir.py:86-91` — StopReason 枚举加了 `unknown`
- [ ] `pyproject.toml` — +python-dotenv、+mypy dotenv override、+`[project.scripts] vague-code`
- [ ] `vague_code/agent/__init__.py` — 导出 Agent / AgentConfig
- [ ] `tests/test_deepseek_codec.py:183-189` — unknown 断言更新（golden 三件套未动）
- [ ] `day0_minimal_loop.py` 已删
- [ ] 真实跑验证：`uv run vague-code --model deepseek-chat "回复你好" .` → end_turn，SQLite 落盘正常（2 条 real run 记录）
- [ ] 验证结果：ruff ✅ / mypy ✅ / pytest 44 passed（本轮修复 5 条 bug，新增 5 复现测试，替换 1 假测试）

## D. ADR-0004 工具注册表

- [ ] `tools.py:10-16` — `Tool` dataclass（spec + factory + bind）
- [ ] `tools.py:19-33` — `_read_file_factory` 闭包捕获 workdir，handler 签名 `(dict) → str`
- [ ] `tools.py:36-49` — `READ_FILE_SPEC` + `DEFAULT_TOOLS` 注册表
- [ ] `loop.py:25-33` — Agent 构造接收 `tools: dict[str, Tool] | None`，`tools is not None`（非 `or`）
- [ ] `loop.py:44-50` — bind 期失败 → `error(tool_bind_error)` + `run_end(tool_bind_error)`
- [ ] `loop.py:91-94` — 空 tool_use 批次 → `error(empty_tool_use)` + `run_end(empty_tool_use)`
- [ ] `loop.py:102-119` — handler 查表 `bound_tools.get()`，未注册 → `is_error` 回喂
- [ ] 测试：`test_empty_tools_registry` / `test_bind_failure...` / `test_empty_tool_use...` / `test_tool_registry_key_name_mismatch...`

## E. Schema 核对

- [ ] `trajectory.py:44-51` — `SCHEMA_RUNS`：`run_id TEXT PK, task TEXT, workdir TEXT, config_json TEXT, status TEXT, created_at REAL`
- [ ] `trajectory.py:53-59` — `SCHEMA_EVENTS`：`run_id TEXT, turn INTEGER, ts REAL, type TEXT, payload TEXT`
- [ ] `trajectory.py:22-29` — `EventType(str, Enum)` 枚举 7 成员；`type` 字段从 `str` 改为 `EventType`
- [ ] `trajectory.py:62-88` — `Run` dataclass + `from_events()` 工厂 + `to_row()`
- [ ] 事件公共字段：全部事件携带 run_id + ts + turn + type + payload
- [ ] payload 演化：JSON 自由格式，不改表结构

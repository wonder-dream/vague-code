# Batch 05: RunHandle Close, Cross-Codec Retry, Signature Roundtrip

## T1 — RunHandle 缺少 close()，轨迹可能永不落盘

`for ev in handle: if done: break` 时生成器未耗尽，
`_persist(traj)` 依赖 GC 触发。加 `close()` + context manager。

## T2 — classify_llm_error 不认识 Anthropic 异常

AnthropicBackend 抛出 `anthropic.RateLimitError` / `InternalServerError` / `APITimeoutError`
全部被分类为 `unknown` → 不可重试。加 try/except ImportError 延迟导入 Anthropic 异常。

## T3 — ThinkingBlock.signature 持久化往返丢失

`_decode_block` 重建 ThinkingBlock 时未传 `signature` 参数。
补上 `signature=d.get("signature")`，一行。

## T4 — ModelResponse.to_dict() 三字段 None 崩溃

`message` 和 `stop_reason` 在 None 时直接 `.to_dict()` / `.value`。
加 None guard，fallback 到空 Message / "unknown"。

## T5 — _resolve_api_key() 在 try/except 外部

`dotenv_values()` 抛异常时 raw traceback 穿透 main()。
移入 try 块内。

## T6 — to_row() default=str 静默损坏非 JSON 数据

`default=str` 把 datetime/set 转换为不可逆字符串。
改为先 try 再 fallback 到 repr + warning。

## T7 — events 表缺少 CREATE INDEX

全表扫描每次 resume。加 `CREATE INDEX IF NOT EXISTS idx_events_run_id`。

## T8 — partial_json:null → ArgsDelta(delta=None)

`dict.get("partial_json", "")` 在值为 null 时返回 None。
改 `or ""` 覆盖。

## T9 — 双重 message_stop 双重 MessageEnd

`decode_event()` 缺 `_ended` guard。补上。

## R1 — 工具输出无大小上限

tool result 无截断，token 堆积。加 50K 字符截断。

## R2 — 重复 ToolUseStart 静默损坏 Aggregator

同 ID 两次 `ToolUseStart` 覆盖 name 但保留旧 buffer。
改为重置 buffer + warning。

## R3 — DeepSeek error 抛 ValueError 而非 StreamDisconnect

跨 codec 异常类型不一致。改 `raise StreamDisconnect(...)`。

## R4 — .db.sqlite 后缀被拒

`Path.suffix != ".db"` 拒了 `.sqlite`。
改 `endswith((".db", ".sqlite"))`。

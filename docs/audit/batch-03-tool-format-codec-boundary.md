# Batch 03: Tool Format Inconsistency & Codec Boundary

## #1 — grep 根级别格式异常

grep 在 `file == root` 时输出 `".:5: content"` （glob 同场景输出 `"README.md"`）。
修复：检测并省略 `.` 前缀。

## #2 — grep 无效正则静默返回空

grep 遇到非法正则时返回 `""`，LLM 无法区分"无匹配"和"正则写错"。
修复：返回 `"Invalid regex pattern: {e}"`。

## #3 — DeepSeek 空系统消息

系统消息内容为空时产生 `{"role":"system","content":""}`。
修复：空内容跳过不编码。

## #4 — DeepSeek 多条系统消息不合并

每轮多条系统消息各编码一条 `{"role":"system"}` wire 消息（OpenAI 协议不保证接收）。
修复：编码前合并成一条。

## #5 — Anthropic 非 TextBlock 系统内容静默丢弃

系统消息中非 TextBlock 内容被跳过无警告。
修复：加 `warnings.warn`。

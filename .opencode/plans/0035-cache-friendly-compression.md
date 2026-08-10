# 0035: 缓存友好压缩链（对齐 Claude Code / Codex / opencode / Pi 业界做法）

- **日期**: 2026-08-10
- **状态**: approved（用户确认：借鉴 Pi 结构化摘要；不跑消融测试）

## 背景

调研 Claude Code / OpenAI Codex / opencode / Pi 四家上下文管理，共识：
**压缩是低频阈值触发（context 快满才做），没有任何工具"每轮改写历史"**。
vague-code 的 stale_snip 每轮无条件执行（context_compress.py:740），持续改写
历史中间消息 → 缓存前缀持续断裂（实测命中率 93%→27%、单 token 成本 ×4）。

## 改动

1. **`CompressionConfig`**：删除 `microcompact_threshold`(0.5) / `structured_snip_threshold`(0.65)，
   新增 `rewrite_threshold: float = 0.7`（改写闸门，stale/micro/structured 共用）。
   `auto_compact_threshold`(0.85) / truncate 兜底不变。

2. **`compress_chain` 改写闸门**：利用率 ≤70% 时**完全不动历史**（只 append），
   前缀稳定 → 缓存高命中；>70% 时一次性执行全部改写型层（stale → micro → structured），
   形成一次断裂后缓存重新积累。行为曲线对齐四家："积累期 → 一次性断裂 → 重新积累"。

3. **`auto_compact` 结构化摘要**（借鉴 Pi）：摘要提示词升级为结构化模板
   （## Goal / Progress(Done/In Progress/Blocked) / Key Decisions / Next Steps /
   Critical Context + `<read-files>`/`<modified-files>`），并从被压缩消息中
   预提取文件清单（read_file/write_file/patch 路径）作为摘要输入，文件追踪
   跨多轮压缩累积（Pi 同款）。

4. **不做**：Pi 的"摘要请求禁用缓存写"（Chat Completions/DeepSeek 协议无此参数，
   不适用）；"缓存命中率感知压缩"（四家均无，属过度设计，Karpathy 原则否决）。

## 验证（用户确认不跑消融）

- 全量 pytest（压缩链测试更新：阈值语义、摘要格式断言）
- ruff/mypy；提交 + 发布

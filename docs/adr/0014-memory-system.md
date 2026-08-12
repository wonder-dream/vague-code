---
status: accepted
date: 2026-07-27
---

# 0014: 记忆系统

## 背景

跨会话记忆。统一记忆库 + episodic（情景按需检索）注入策略。与 auto-compact 压缩协同做增量蒸馏。

> **决策更新（2026-08-01）：** pinned（常驻）注入被判定为伪需求——生效范围是全局 memory.db、无项目隔离，与用途（项目约定）不匹配；且 `.agent/rules.md` 层级加载（ADR-0008）可完整替代。**pinned 已移除。**

> **决策更新（2026-08-12，记忆 v2）：** **SQLite 记忆库整体移除，改为文件式记忆**——`<workdir>/.agent/memory.md`（gitignored）按项目物理隔离，system prompt 注入全文（限 200 行 / 25KB），会话结束时一次 LLM 总结追加。`memory.py`（MemoryStore）/ `memory_tool.py`（memory_search 工具）/ `memory_db_path` 全部删除。

## 约束（v2）

1. **零外部服务、零数据库**——单 markdown 文件，可人工编辑、可 diff
2. **项目物理隔离**——文件位于 workdir 内，跨项目天然不可见（修复 v1 全局库跨项目污染缺陷）
3. **幂等写入**——内容 sha256 前 12 位作 hash 注释，重复内容不重复写
4. **双时点写入**——auto_compact 摘要直接落盘 + 会话结束（run/chat_end）一次 LLM 总结
5. **上下文注入限长**——200 行 / 25KB（与 Claude Code MEMORY.md 同款上限）

## 架构（v2）

### 存储模型（`.agent/memory.md`）

```markdown
<!-- vague-code memory: agent 蒸馏的历史会话记忆，可手动编辑 -->

## <标题>
<!-- source: <run_id>; created: <iso>; hash: <sha256[:12]> -->
<蒸馏内容>
```

每个 `## 标题` 块 = 一条记忆。文件头注释（HTML 注释）不参与上下文语义。

### 写入流程

1. **auto_compact 触发** → 压缩摘要 → `MemoryFile.append()`（标题取摘要首行，hash 去重）
2. **会话结束**（`Agent.run()` 收尾 / `chat_end()`）→ 一次 LLM 调用总结本会话 1-3 条要点 → 追加；输出「无」或异常则静默跳过
3. 蒸馏模型：`memory.distill_model`，缺省 = 主 agent 模型

### 读取（注入）

system prompt 追加「## 项目记忆」段 = 文件全文截尾（限 200 行 / 25KB，字节截断 UTF-8 安全）。无按需检索工具——文件可被 LLM 用 read 工具直接读取，注入上限内全文可见。

### 清理

TUI 删除会话 → `MemoryFile.remove_sections(run_id)` 移除对应来源分块。

## Consequences

- v1 的 SQLite + LIKE 检索被判定为过度设计：蒸馏产物本就要注入上下文，DB 检索是多余分层；对齐 Claude Code auto memory（markdown 文件注入）与 Codex memories（文件生成后注入）的主流形态
- 每会话结束 +1 次 LLM 调用（~几分钱）；失败静默降级，不影响运行
- 记忆可人工编辑/删除（文件即事实源）；过时记忆不自动检测，靠注入可见性 + 手动整理
- 进程内按路径加锁串行写（TUI 多会话同项目并发安全）；多进程同 workdir 并发不在支持范围（eval 禁用记忆，CLI 单任务）
- 配置：`MemoryConfig{enabled, memory_file=".agent/memory.md", session_end_distill, distill_model}`

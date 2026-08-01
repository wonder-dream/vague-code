# 交接清单：XClaw 教学会话（2026-07-31）

本文档记录一次教学 + 设计讨论会话的成果，供下次会话恢复上下文使用。

---

## 一、教学进度（文章 01 · 已收官）

- 文章 01 术语表已完整讲完，三档作业全部通关：
  - A 档（复述）：✓✓ 术语 + 四层压缩
  - B 档（辨析）：✓✓ ReAct vs CoT / KV vs Prompt Caching / ALLOW vs CONFIRM / Budget vs Limit
  - C 档（实战定位）：✓✓ 冲突可串行化三函数全部定位
- **下一篇**：`docs/articles/02-architecture-overview.md`（架构全景）——把 10 个子系统串成协作图
- 额外练习已答：C 档贪心次优反例（`{A,B},{C,D},{E}` vs 最优 `{A,E},{B,C,D}`）

---

## 二、项目决策（已拍板、未实施）

### 1. pinned 判定为伪需求 → 移除
- 依据：生效范围是全局 memory.db、无项目隔离，与用途（项目约定）不匹配；且与 `.agent/rules.md` 层级加载（ADR-0008）功能重复且更弱
- 影响范围（约 25 处文档 + 4 处代码）：
  - 代码：`config.py:58`（inject_pinned）、`memory.py:88-92`（get_pinned）、`loop.py:201-208`（pinned 注入）、`sidebar.py:73-84`（TUI 记忆面板）
  - 文档：README / CONTEXT.md / ADR-0014 / articles(01,02,03,04,08,11) / guide(02,03,04,08) / R1 / troubleshooting / DOCUMENTATION_PLAN / architecture.drawio
  - 注意：`kind` 列保留，只余 `'episodic'`；ADR-0014 编号保留只改内容；`docs/adr/README.md:30` 行内容需同步

### 2. episodic 保持现状
- 不强化、不删。eval harness 中 memory 关闭（`harness.py:124`），不在消融因变量中
- 当前蒸馏只发生在 auto_compact 触发时（`loop.py:321-329`），产物永远 `kind="episodic"`

### 3. Repo Map 代码库索引（tree-sitter）— 已落盘待实施
- 计划文件：`docs/plans/0014-repo-map.md`（已写入，9KB）
- 已定决策点：
  - tree-sitter 0.26.0 + tree-sitter-python（纯本地，符合零外部服务铁律）
  - 仅支持 Python（SWE-bench 30 题全是 Python）
  - 注入 + 工具双通道（system prompt 注入 top-N 地图 + `code_search` 工具）
  - 编号：plan 0014 / ADR 0016（已 grep 核实无冲突）
- 17 步实施清单在计划文件中，从步骤 1（加依赖）开始

---

## 三、过程中建立的理解（下次教学可直接复用）

- **DeepSeek 缓存机制**：实为"缓存前缀单元（cache prefix unit）"机制，非简单最长前缀匹配；需"完整匹配已持久化的前缀单元"才算命中（已从官方文档 `api-docs.deepseek.com/guides/kv_cache` 核实）
- **Anthropic prompt caching**：仍需显式 `cache_control: {"type": "ephemeral"}` 断点（未抓到最新正文，基于既有资料）
- **identity ≠ system prompt ≠ system message**：
  - identity = `context.py:10-17` 硬编码 `AGENT_IDENTITY`（第一段，恒定）
  - system prompt = identity + rules + workdir 拼接（`SystemPrompt.build()`）
  - system message = IR 中 `role="system"` 的 Message（`loop.py:226-229`）
- **冲突可串行化三函数定位**：
  - `_extract_scope` `concurrency.py:54-85`（ToolUseBlock → ResourceScope）
  - `_scopes_conflict` `concurrency.py:90-104`（判定器，双读不冲突/workspace 必冲突）
  - `schedule` `concurrency.py:118-138` + `execute_concurrent` `concurrency.py:146-213`
- **B 档辨析要点**：
  - CoT = 想，ReAct = 想→做→看（CoT 输出不改变世界，ReAct 行动改变世界并回馈）
  - Budget = 主动安全距离（`window × 0.9` 留 10% 给输出），Limit = 硬墙

---

## 四、下次会话续接方式

1. 读取本文档恢复上下文
2. 教学：继续文章 02（架构全景）
3. 或实施：按 `docs/plans/0014-repo-map.md` 步骤 1 开始

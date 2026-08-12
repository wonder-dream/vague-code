# Memory System

**谁需要读：** 想理解跨会话记忆机制的开发者
**前置阅读：** 07-permission-system.md
**读完能做什么：** 了解文件式记忆的蒸馏与注入流程

---

## 1. 概述

**记忆 ≠ 上下文。** 这是 Memory System 的第一设计边界。上下文（Context Engineering）管理当前对话的 token 窗口，是短期的、瞬态的。记忆负责长期知识——跨会话、可持续积累。

vague-code 的记忆系统（ADR-0014 v2）是**文件式记忆**：`<workdir>/.agent/memory.md`（gitignored），按项目物理隔离，system prompt 注入全文（限 200 行 / 25KB）。

> **设计演进：**
> - **v1（2026-07-27）**：SQLite 统一记忆库 + `memory_search` 工具按需检索（LIKE + 热度排序）
> - **2026-08-01**：pinned（常驻注入）移除——全局 memory.db 无项目隔离，与"项目约定"用途不匹配，职责移交 `.agent/rules.md`（ADR-0008）
> - **v2（2026-08-12）**：**SQLite 整体移除，改为 markdown 文件**。蒸馏产物本就要注入上下文，DB 检索是多余分层；对齐 Claude Code auto memory（MEMORY.md 注入限长）与 Codex memories（文件生成后注入）的主流形态

---

## 2. 存储模型

**MemoryFile** 类（`memory_file.py`）管理单个 markdown 文件，每个 `## 标题` 块 = 一条记忆：

```markdown
<!-- vague-code memory: agent 蒸馏的历史会话记忆，可手动编辑 -->

## <标题>
<!-- source: <run_id>; created: <iso>; hash: <sha256[:12]> -->
<蒸馏内容>
```

- **项目隔离**：文件在 workdir 内 → 跨项目天然不可见（修复 v1 全局库跨项目污染缺陷）
- **可人工编辑**：文件即事实源，可 diff、可手动增删改
- **幂等去重**：`append()` 以内容 sha256 前 12 位比对 hash 注释，重复内容不重复写
- **并发**：进程内按路径加锁串行写（TUI 多会话同项目并发安全）

---

## 3. 注入（读取）

`_init_run` 构建 system prompt 时追加「## 项目记忆」段：

```python
if self.config.memory.enabled:
    mf = self._get_memory_file(workdir)
    if mf is not None:
        memory_text = mf.inject_text()
if memory_text:
    system_prompt += "\n\n## 项目记忆（历史会话蒸馏，可编辑 .agent/memory.md）\n" + memory_text
```

**`inject_text()` 限长**（与 Claude Code MEMORY.md 同款上限）：
- 限 200 行：超出取前 200 行
- 限 25KB：超出按字节截断（UTF-8 安全，无半个字符）

无按需检索工具——限长内全文可见，LLM 可随时用 read 工具读文件或直接引用注入内容。

---

## 4. 写入管道（蒸馏）

### 时点 1：auto_compact 触发

压缩摘要直接落盘（复用摘要结果，零额外 LLM 调用）：

```python
if r.layer == "auto_compact" and r.affected > 0 and r.detail.get("summary_text"):
    mf.append(
        title=summary.strip().splitlines()[0][:40],
        content=summary,
        source_session=traj.run_id,
    )
```

### 时点 2：会话结束（`Agent.run()` 收尾 / `chat_end()`）

一次 LLM 总结调用（`_distill_session`，模型可配 `distill_model`，缺省主模型）：

- 输入：会话任务文本（截 2000 字符）
- 输出要求：1-3 条 `## 标题 + 内容` markdown；无可记内容输出「无」
- 解析失败 / LLM 异常 → 静默降级（warn + 跳过），不影响运行
- 成功追加 → `memory_distill` 事件落盘（run_id / appended / 文件路径）

**成本**：每会话 +1 次 LLM 调用（约几分钱）。

---

## 5. 清理

TUI 删除会话 → `MemoryFile.remove_sections(run_id)` 移除该来源会话的所有分块（按 `<!-- source: -->` 注释匹配）。

---

## 6. 配置参考

**MemoryConfig**（`config.py`）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| enabled | bool | True | 是否启用记忆系统 |
| memory_file | str | `".agent/memory.md"` | 记忆文件路径（相对 workdir 解析，绝对路径直用） |
| session_end_distill | bool | True | 会话结束是否执行 LLM 总结蒸馏 |
| distill_model | str \| None | None | 蒸馏模型（None = 主 agent 模型） |

---

## 下一篇

→ **09-model-abstraction.md**：统一的 LLM 接口抽象——IR 类型、Codec 架构、流事件系统。

**相关 ADR：** 0014（Memory System）
**相关 plans：** 0012（memory-system）

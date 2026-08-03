# FAQ

**谁需要读：** 对 XClaw 设计决策有疑问的任何人
**前置阅读：** 无（按问题检索）
**读完能做什么：** 理解关键设计决策的"为什么"

---

### Q1：为什么要自建 Coding Agent，不用 Claude Code/Cursor？

全栈理解驱动——必须处理上下文治理、权限安全、并发调度等所有工程问题，而不仅仅是使用一个产品。商业产品中间状态不可见，无法做消融实验验证设计决策。简历价值也来自自建系统产出的压缩策越、并发提速等可控数据。详见 ADR-0001 库优先设计。

### Q2：为什么用 5 层压缩而不是 1 层？

不是所有 token 浪费的价值相等。5 层按精准度排序：stale_snip 精准回收重复读取（零损失）→ microcompact 保留头尾关键信息（只删中间噪音）→ structured_snip 利用轨迹事件识别已完成子任务并替换为结构化摘要（零 LLM 成本，避免走到 auto_compact）→ auto_compact 让 LLM 理解后选择保留（最准但最贵）→ truncation 硬截断（最盲但能兜底）。详见 06-context-engineering.md 和 ADR-0011/0017。

### Q3：为什么不使用 asyncio？

Agent 核心循环是同步阻塞的（`while turn_box[0] < max_turns`），同步模型让代码更简单。并发走 `ThreadPoolExecutor`（`concurrency.py:171`），更匹配 IO/CPU 混合的工具执行场景。TUI 层用 asyncio，Agent 线程用 `@work(thread=True)` 隔离。详见 ADR-0012 约束 5。

### Q4：为什么自定义 IR 而不是直接用 OpenAI/Anthropic 格式？

上层代码零分支——切换厂商只需替换 codec，`loop.py` 中没有任何厂商特化逻辑。`ToolSpec` 一个对象可以生成两种厂商格式。ThinkingBlock 统一管理，不同厂商的 thinking 字段映射到同一 IR 类型。元数据携带（stale/compacted 标记）通过 meta 字段透传。详见 ADR-0002 和 09-model-abstraction.md。

### Q5：为什么 event-sourced JSONL 而不是直接存消息数组？

消息数组天然丢失元数据（usage、timing、压缩率）。事件流可审计——回溯任何权限决策和压缩回收。崩溃恢复时重建事件流 → `to_messages()` 还原状态。详见 ADR-0003 和 10-trajectory.md。

### Q6：为什么压缩失败了 Agent 还能继续？

压缩是纯函数 + 失败降级——stale_snip/microcompact/auto_compact 各层独立可跳过。`compress_chain` 整体异常时降级到 truncation 兜底（`loop.py:278-285`）。设计原则：压缩是优化不是核心路径。

### Q7：并发执行工具为什么有安全风险？怎么规避？

两个工具同时写同一文件会产生竞态条件。规避方案是冲突可串行化模型（`concurrency.py`）：scope 提取 → 冲突检测 → 分组调度。bash 的 scope_type=WORKSPACE 导致它与所有其他操作串行。详见 ADR-0012 和 05-tool-system.md 第 6 节。

### Q8：50K 工具输出截断会不会丢信息？

50K 是执行时硬截断（`loop.py:558-565`），不是永久删除。microcompact 折叠后保留 head+tail + 原文指针。全量数据在 trajectory 事件流中可恢复。

### Q9：为什么记忆系统用 LIKE 而不是 BM25/向量搜索？

LIKE 不引入额外依赖，1000-10000 条记忆量级足够用。热度排序公式 `(use_count × 100) / MAX(1, minutes_since_last_use + 1)` 提供合理排名（`memory.py:78`）。FTS5/BM25 仍在 roadmap 上。

### Q10：怎么贡献/添加新特性？

GitHub PR。阅读 05-tool-system.md（添加工具）和 09-model-abstraction.md（添加 codec）。代码规范：ruff + mypy + pytest（586 项测试）。先提 issue → ADR 流程。

### Q11：XClaw 与其他 Coding Agent 的性能对比？

不和其他商业产品比（资源差距悬殊）。聚焦消融实验：内部特性开关对比。2026-08 起评测升级为真验收（sanity gate 双检 + F2P/P2P 实跑），任务集按 OpenAI SWE-bench Verified 官方标注重建（31 题，本机可跑 20 题），数字以 `docs/handoff/2026-08-03-xclaw-eval-system.md` 与 `eval/README.md` 为准（早期 60%/93% 基于假 pass/fail，已废弃）。

### Q12：trajectory 事件流中为什么会有重复的事件？

U2（known issues）：resume 时重新运行 compression。分析侧按 `(run_id, turn, layer)` 去重。详见 docs/known-issues.md:U2。

---

## 下一篇

→ **[../adr/README.md](../adr/README.md)**——15 篇 ADR 的索引表。

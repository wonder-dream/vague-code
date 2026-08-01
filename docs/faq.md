# 细纲：faq.md

**预估行数：** ~300 行
**定位：** 设计决策问答。

---

## 开头

- **谁需要读：** 对 XClaw 设计决策有疑问的任何人
- **前置阅读：** 无（按问题检索）
- **读完能做什么：** 理解关键设计决策的"为什么"

---

## 细纲

### Q1：为什么要自建 Coding Agent，不用 Claude Code/Cursor？

- 全栈理解：必须处理上下文治理、权限安全、并发调度等所有工程问题
- 可控性与可消融：商业产品中间状态不可见
- 简历价值：压 token、并发提速等数据来自自建系统
- ADR-0001 库优先设计的延伸

### Q2：为什么用 5 层压缩而不是 1 层？

- 精准度排序：不是所有 token 浪费的价值相等
- stale_snip：精准回收重复读取（零损失）
- microcompact：保留头尾关键信息，只删中间噪音
- structured_snip：利用轨迹事件识别已完成子任务，零 LLM 成本结构化摘要（避免走到 auto_compact）
- auto_compact：LLM 理解后选择保留什么（最准但最贵）
- truncation：硬截断（最盲但能兜底）
- 详见 06-context-engineering.md 和 ADR-0011

### Q3：为什么不使用 asyncio？

- Agent 核心循环是同步阻塞的（`while turn_box[0] < max_turns`）
- 并发走 `ThreadPoolExecutor`（`concurrency.py:171`），更匹配 IO/CPU 混合场景
- TUI 层用 asyncio，Agent 线程用 `@work(thread=True)` 隔离
- 详见 ADR-0012 约束 5

### Q4：为什么自定义 IR 而不是直接用 OpenAI/Anthropic 格式？

- 上层代码零分支：切换厂商只需替换 codec
- `ToolSpec` 一个对象生成两种 format
- ThinkingBlock 统一管理
- 元数据携带（stale/compacted 标记）通过 meta 字段
- 详见 ADR-0002 和 09-model-abstraction.md

### Q5：为什么 event-sourced JSONL 而不是直接存消息数组？

- 消息数组丢失元数据（usage、timing、压缩率）
- 事件流可审计：回溯任何权限决策和压缩回收
- 崩溃恢复：重建事件流 → to_messages() 还原状态
- 详见 ADR-0003 和 10-trajectory.md

### Q6：为什么压缩失败了 Agent 还能继续？

- 压缩纯函数 + 失败降级
- stale_snip/microcompact/auto_compact 各层独立跳过
- compress_chain 整体异常 → truncation 兜底（`loop.py:278-285`）
- 设计原则：压缩是优化不是核心路径

### Q7：并发执行工具为什么有安全风险？怎么规避？

- 风险：两个工具同时写同一文件→竞态条件
- 规避：冲突可串行化模型（`concurrency.py`）
- scope 提取 → 冲突检测 → 分组调度
- bash scope_type=WORKSPACE → 与其他所有操作串行
- 详见 ADR-0012 和 05-tool-system.md 第 6 节

### Q8：50K 工具输出截断会不会丢信息？

- 50K 是执行时硬截断（`loop.py:558-565`），不是永久删除
- microcompact 折叠后保留 head+tail + 原文指针
- 全量数据在 trajectory 事件流中可恢复

### Q9：为什么记忆系统用 LIKE 而不是 BM25/向量搜索？

- LIKE 不引入额外依赖
- 1000-10000 条记忆量级足够用
- 热度排序公式提供合理排名（`memory.py:78`）
- FTS5/BM25 仍在 roadmap 上

### Q10：怎么贡献/添加新特性？

- GitHub PR（占位）
- 阅读：05-tool-system.md（添加工具）、09-model-abstraction.md（添加 codec）
- 代码规范：ruff + mypy + pytest（448 项测试）
- 先提 issue → ADR 流程

### Q11：XClaw 与其他 Coding Agent 的性能对比？

- 不和其他商业产品比（资源差距悬殊）
- 聚焦消融实验：内部特性开关对比
- 基线 60% pass rate → 并发 ON 93%（+33pp）
- 详见 12-evaluation-harness.md 和 eval/results.md

### Q12：trajectory 事件流中为什么会有重复的事件？

- U2（known issues）：resume 时重新运行 compression
- 分析侧按 `(run_id, turn, layer)` 去重
- 详见 docs/known-issues.md:U2

---

## 结尾

**下一篇推荐：** → adr/README.md（ADR 索引）

---

## 本文件说明

这是文档 `faq.md` 的细纲。每个问答约 20-25 行，写作时需引用具体代码位置作为证据。

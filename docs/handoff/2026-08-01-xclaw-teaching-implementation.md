# 交接清单：XClaw 教学 + 实施会话（2026-08-01）

本文档记录一次教学 + 大规模实施会话的成果，供下次会话恢复上下文使用。

---

## 一、教学进度（文章 02 · 已收官，文章 03 · 刚起步）

### 已完成
- **文章 02 架构全景**已完整讲完：四层架构 + Repo Map 独立层、一次请求时序（含双 checkpoint）、数据存储位置表、5 条关键不变量
- **两个新特性深挖到算法层**：
  - structured_snip：`_detect_subtasks()` 状态机已逐行推演（work_start 开 → 成功 bash 闭 → 失败 bash 不断裂；code_search 不界定子任务）
  - Repo Map：双通道（注入 top-N 地图 + code_search 工具）、mtime 增量刷新
- **架构质疑讨论**：零 asyncio 设计的完整论证（SSE 同步生成器消费、线程池并发、async-only 生态是真实短板的桥接方案）
- **subagent 委派系统设计**（完整设计讨论，已落盘为 plan 0015）

### 待续
- **文章 03**（A Single Turn Explained）刚引入"messages 配对模型"（assistant, user 成对出现，一对 = 一个 turn），尚未正式展开
- **下一篇**：`docs/articles/03-a-single-turn-explained.md`（单轮循环展开到每个函数调用/数据变换）

### 教学状态评估
- 按文章数进度 ≈ 30%（00/01/02 已收官，03 起步）
- 按知识点深度 ≈ 50%（structured_snip、Repo Map、权限矩阵、IR/Codec 已到算法/术语级）
- 剩余 04-12 中，**04（Runtime）和 06（压缩）是重中之重**

---

## 二、本次实施成果（两大部分）

### 1. structured_snip（plan 0013 / ADR 0017）— 已实施
- 压缩管线 **4 层 → 5 层**：`stale_snip → microcompact → structured_snip → auto_compact → truncation`
- `context_compress.py`：`_detect_subtasks()` + `structured_snip()`（零 LLM 成本，闭合子任务 → 结构化摘要）
- `compress_chain()` 签名加 `events=None`（向后兼容）；`loop.py` 传入 `traj.events`
- 配置：`structured_snip_threshold=0.65`、`structured_snip_keep_recent=3`
- 阈值设计意图：0.65 介于 microcompact(0.5) 和 auto_compact(0.85) 之间，中负载截住避免走到 LLM 摘要

### 2. Repo Map（plan 0014 / ADR 0016）— 已实施 + pinned 移除
- `repomap.py`：`Symbol`/`RepoIndex`（build/search/top_symbols/refresh/to_map_text）
- `code_search` 工具（动态注册，scope `(EXACT, READ)`）+ system prompt 地图注入（max 1000 tokens）
- **依赖版本修正**：tree-sitter-python 最新只有 0.25.0（plan 原文写 >=0.26.0 不可用），锁定 `tree-sitter==0.26.0 + tree-sitter-python==0.25.0`
- **pinned 移除完成**：`get_pinned()`/`inject_pinned` 全删；sidebar 改展示最近 episodic；memory.py 新增 `recent()`

### 3. Subagent 委派（plan 0015 / ADR 0018）— 已落盘待实施
- 第一性原理：**subagent = 嵌套调用 `Agent.run()`**（ADR-0001 红利），只需新增 `delegate_task` 桥接工具
- 关键设计：v1 只读委派、防递归、`max_turns=8` 成本硬上限、父子轨迹 `parent_run_id`
- ADR-0018 状态 **proposed**（待用户确认后实施）

---

## 三、git 提交（本次 7 个，已 push）

| 提交 | 内容 |
|------|------|
| `5906927` | 历史遗留：plan 0003→0009 重命名、0013/0014 plan、LICENSE |
| `f7d6edd` | 会话前中文翻译（scripts + 测试断言 + prompts） |
| `e654219` | **核心实现**：repo map + structured_snip + eval 矩阵 2×2×2 |
| `a005395` | 测试：test_structured_snip + test_repomap + 受影响测试 |
| `e13a237` | 教学文档同步（articles/guide/reference/tutorials） |
| `d5b3a72` | 项目文档 + ADR-0016/0017/0018 + plan-0015 |
| `75b9f89` | `.idea/` 忽略 |

- 全部已 push 到 `origin/main`（wonder-dream/xcode）
- 测试：**516 全绿**，ruff/mypy 零错误
- 注意：`memory.py`/`tools.py`/`concurrency.py` 等文件同时含会话前中文翻译 + 本次实现，按文件粒度归入代码提交

---

## 四、待跟进问题（教学遗留）

### 1. 【重点】记忆系统无项目隔离 — 用户提出的设计问题
- **问题**：`memories` 表无 project 字段，`search()` 不按项目过滤，episodic 记忆全局共享，存在跨项目污染风险
- **关键洞察**：这正是当初判定 pinned 是伪需求的核心理由（全局无隔离），但 episodic 留下了同样的隐患——**已知的不一致，不是正确设计**
- **修复方向**（面试可讲）：
  - 方案 A：`memories` 表加 `project` 字段，`ingest` 记录 workdir/repo，`search` 按 project 过滤
  - 方案 B：`memory_db_path` 跟随 workdir 解析（每项目一个 DB）
  - 业界双轨：项目约定走 `.agent/rules.md`（ADR-0008，已解决项目隔离），跨项目偏好走全局记忆
- **标准回答**：主动承认缺陷 + 给修复方向，比硬说"设计如此"可信

### 2. 补考遗留的两个概念（用户自称已懂但表述弱）
- CoT 无法获取世界信息（推理只能基于上下文已有内容）
- 缓存命中看"共同前缀"（不是只有永远不变的东西才命中）

### 3. 【已记录】`loop.py:505` 兜底正常流程不可达 — 教学中发现
- **发现**（2026-08-02 教学 session，用户即作者确认）：`while turn_box[0] < max_turns` 无法因 turn_box 涨满而退出——`loop.py:420` 熔断（`turn+1 >= max_turns`）在工具执行前 return，`turn_box` 永远停在 `max_turns-1`，`loop.py:505` 的 `run_end(max_turns)` 为防御性死代码
- **本质**：max_turns 是"硬墙"非"可正常耗尽的预算"；续轮唯一途径是 tool_use，最后一轮 tool_use 必被熔断，不存在"预算正常耗尽"路径
- **记录**：已写入 `docs/known-issues.md` U3；已同步 `docs/articles/03` 步骤 9 终止条件表（新增"熔断/兜底"两行 + 注释）
- **后续可选**：是否保留 505 行为（不修，防御性）；教学表述统一以 420 熔断为准

---

## 五、下次会话续接方式

1. 读取本文档恢复上下文
2. **教学**：继续文章 03（单轮循环展开）——从 messages 配对模型开始
3. **或实施**：按 `docs/plans/0015-subagent-delegation.md` 步骤 1 开始（config.py 加 DelegateConfig）
4. 用户当前状态：**头晕、动力不足**——建议休息，教学节奏放缓（每篇 40-60 分钟，留缓冲）

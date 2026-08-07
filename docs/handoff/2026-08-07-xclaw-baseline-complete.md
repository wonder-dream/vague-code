# Handoff 2026-08-07：10 题全量基线完成（78 runs，$25.08）+ 消融无显著差异 + 监督/压缩复验

> 本会话完成：**P0 主任务——10 题基线重跑全部落地**。核心层 10 实例 × k3（30 runs）+ 消融层 8 实例 × 3 关闭配置 × k2（48 runs）= **78 runs 全部完成**，无中断、无 env 错误、无 stale。总成本 **$25.08**（agent $23.38 + supervisor $1.71，预算 $26-30 内）。3 进程并行（WMI 启动，--fresh，max-cost 12/12/8），全程 ~5h（11:12→16:24）。代码零改动。完整报告：`runs/eval/b10_baseline_report.md`。

---

## 一、核心层 pass^3（主结果，10 实例 × k3 = 30 runs）

**8/10 满分（80%），满足 ADR-0020 标准 3（pass^3 ≥ 2/3）✅**

| 实例 | pass^3 | 失败 run | 核心成本 |
|---|---|---|---|
| 12419 / 12481 / 15345 / 20590 / 23262 / 13480 / sphinx-8595 / pytest-7432 | 3/3 ✅ | — | $1.08 / $0.93 / $0.78 / $0.96 / $1.19 / $0.44 / $0.70 / $0.82 |
| sympy-13031 | **2/3** | r0 `no_diff` 零编辑（r1/r2 正常） | $1.27 |
| sympy-21612 | **1/3** | r0/r2 `no_diff` 零编辑 | $1.31 |

- 核心层成本合计 **$9.48**；波动题（13031/21612）失败模式均为 no_diff（40 轮零编辑），非半途错误
- 21612 是唯一反复失败实例：核心 1/3 + 消融 3 配置 5/6 fail——**P2 失败分类首选目标**

## 二、消融层（8 实例 × 3 关闭配置 × k2 = 48 runs）

| 配置 | pass | 结论 |
|---|---|---|
| **sx** 关 RepoMap | **16/16 (100%)** | 关闭零损失 → RepoMap 是纯收益（省 token 不损能力） |
| **nc** 关压缩 | 15/16 (94%) | 21612 nc r1 `no_diff` |
| **nm** 关并发 | 14/16 (88%) | 21612 nm r0/r1 `f2p:fail`（有编辑但未过测试） |

- **三变量整体无显著消融效应**：除 21612 外全部实例三配置全过（pass 天花板掩盖差异）→ 消融有区分度需更难任务集或更高 k
- 21612 失败模式随配置变化：基线/关压缩 = no_diff，关并发 = f2p:fail → **并发调度对 21612 修复路径有实质影响**（可作面试素材）

## 三、监督复验（ADR-0020 标准 4/5，78 runs 全量）

- **stagnant 1/78 = 1.3%** < 15% ✅（唯一判停：12481 nm r0，36 轮，verified=True 正确收尾）
- **监督增量 6.8%**（$1.71/$25.08）< 15% ✅，sup_calls 均值 8.5/run
- 661 次调用：on_track 184 / needs_verification 419（63%）/ stuck 43 / off_track 4 / done 1 / 解析失败 10；无 stuck 误杀
- run 终止分布：76 max_turns + 1 supervisor_done（13480 r2 提前 36 轮正确收尾）+ 1 stagnant

## 四、压缩验证（78 runs 全量复验，结论定案）

**78 runs 的 compression 事件全部仅 stale_snip（39-40 次/run），microcompact/structured_snip/auto_compact/truncate 零触发**——与 08-06 小样本（9/9）完全一致，结论**已由两个数据集共 87 runs 确认**：40 轮任务 token 利用率达不到 50%/65%/85% 阈值，五层流水线后半段在当前任务集上无收益可测。

## 五、数据文件

- 结果 JSON：`runs/eval/results_20260807-{135536,161222,162351}.json`（P3/P2/P1，78 runs 全量）
- CLI 报告：`runs/eval/b10_p{1,2,3}.md`（逐 run 明细 + 轨迹指标 + 监督明细）
- 汇总报告：`runs/eval/b10_baseline_report.md`（本会话产出）
- 轨迹 db：`runs/eval/*__{C_X_M,nc_X_M,C_sx_M,C_X_nm}_r*.db`（78 个）
- 日志：`runs/eval/b10_p{1,2,3}.log`（stdout 全缓冲，进程结束才完整）

## 六、待办（更新后）

| 优先级 | 事项 | 说明 |
|---|---|---|
| **P1** | **21612 失败分类（P2 八类）** | 核心 1/3 + 消融 5/6 fail 且失败模式随配置变化；`python -m eval.classify` |
| P1 | 压缩后半段验证 | 调低 `auto_compact_threshold`（0.85→0.5）单题跑，验证 microcompact/auto_compact 触发与收益；或找 >40 轮任务（**压缩结论已 87 runs 确认，此实验是唯一剩余开放项**） |
| P2 | 压缩 × KV Cache 张力实验 | 保前缀/摘要替换（08-05 handoff 原项，若成立是简历亮点） |
| P2 | 权限矩阵 safe 档 / gold 轨迹标注 / judge 抽评 30 条 | 早前 handoff 原项未动 |
| P3 | 环境策展 | sklearn/astropy 8 题待 Linux/CI（MSVC 编译依赖）；sphinx-8721 判别器不复现 |
| P3 | 消融有区分度 | 当前 8 题 pass 天花板 → 换更难任务集或提 k |

## 七、坑与注意事项（本会话确认）

1. **CLI 实例顺序 ≠ --instances 参数顺序**：cli.py 按 tasks.json 原始顺序过滤（`tasks = [t for t in tasks if t in ids]`），13480 排在 20590 前——进程内实例实际执行顺序以 tasks.json 为准，不影响结果
2. 8/06 handoff 的坑全部继续有效：--fresh 多进程互斥、stdout 全缓冲看 db、WMI 启动、PowerShell 引号写临时 .py、Chill With You 复活
3. 本轮速度实测：单 run 3-18 分钟（13480 快、12419/21612 慢），78 runs 三进程 5h 完成，**成本口径 $0.32/run 均值，supervisor 增量约 $0.02/run**

## 八、面试故事线增量

1. **全量基线闭环**：k=3 统计口径首次上 10 题规模——pass^3 8/10、监督成本 6.8%、stagnant 1.3%，与 08-06 小样本（2/3、5.4%、11%）互相印证，口径稳定
2. **消融实证**：RepoMap 关闭零损失（16/16）→ "RepoMap 是纯收益"有数据支撑；压缩/并发关闭仅影响 21612 单题 → 消融设计的区分度边界（任务集偏易的局限，诚实口径）
3. **失败模式随配置迁移**（21612）：基线 no_diff → 关并发 f2p:fail，说明并发调度改变 agent 的修复行为路径而非简单好坏——工程叙事加分项
4. **压缩结论定案**：87 runs（9+78）一致"仅 stale_snip"→ 五层流水线后半段在短任务集无收益，设计假设被大规模数据修正

## 九、参考文件

- 汇总报告：`runs/eval/b10_baseline_report.md`
- 上一交接：`docs/handoff/2026-08-06-xclaw-phase1-accepted-baseline-pending.md`（P0 定义、命令、坑）
- 验收口径：`docs/adr/0020-supervision-agent.md`

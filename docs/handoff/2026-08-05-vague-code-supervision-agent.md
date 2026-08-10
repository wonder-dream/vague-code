# Handoff 2026-08-05: P0 修复验证通过 + Supervision Agent 方案定案（待实现）

> 本次会话完成了 EDD 第三轮迭代的方案设计与部分落地：P0 行为修复（3/3 验证通过）、max_turns 语义变更决策、Supervision Agent 方案经 grill-with-docs 逐项定案并写入 ADR/计划/CONTEXT。**下一步会话的主任务：按 `docs/plans/0018-supervision-agent.md` 实现并验收。**

---

## 一、本会话已完成（可验证）

| 项 | 内容 | 证据 |
|---|---|---|
| P0-1 | `write_file` 默认 `overwrite=true`（tools.py:12 + spec + 测试反转） | commit `b8da447` |
| P0-2a | `AGENT_IDENTITY` 重写（Claude Code/Codex/OpenCode 方法论 + Karpathy：语境注入/工具分工/完整交付/目标驱动/轮次预算） | commit `b8da447` |
| P0-2b | harness `_task_prompt`：任务文本追加 F2P 验证标准 + 无交互声明（不改 task 数据） | commit `b8da447` |
| 验证 | 3 题（16792/21612/24213，基线全开 0/3 全败）→ **40 轮全部 verified**（$0.80） | `runs/eval/results_20260804-212602.json` |
| 行为证据 | bash 36→11、21→13；编辑 0→2-3；edit_then_test 出现；24213 写了复现测试（Karpathy Goal-Driven 直接命中） | 轨迹 db `runs/eval/*__C_X_M_r0.db` |
| 轮次默认 | `--max-turns` 默认 50→**40**（cli.py + README） | commit `8d245a7` |
| 调研 | 成熟产品轮次上限：SWE-agent 30 / Codex 10 / OpenHands 100 / Claude Code 无限（原始提示词源码已抓） | 见 plans/0018 |
| 方案定案 | Supervision Agent 架构（grill-with-docs 七问定案） | 见下节 |
| 文档 | `docs/adr/0020-supervision-agent.md`、`docs/plans/0018-supervision-agent.md`、CONTEXT.md 新增术语 | 未提交 |

## 二、已定案未实现：Supervision Agent（主任务）

**决策记录**：`docs/adr/0020-supervision-agent.md`（8 条决策）
**实现计划**：`docs/plans/0018-supervision-agent.md`（实现清单 + **5 条验收标准**）

核心定案速览：

| 决策点 | 定案 |
|---|---|
| max_turns | **500 保险丝**（config.py 已有 >500 警告阈值，只拦失控） |
| 监督形态 | 轻量单次 `backend.complete`（仿 judge.py），无工具循环 |
| 触发 | 周期（每 6 轮）+ 完成校验（end_turn 时）双触发 |
| 输入 | 过程信号摘要（metrics.py 现成）+ 尾部轨迹转写（仿 judge._transcript_text 取 8-12 轮）+ 工作区 diff stat，封顶 4-6K tokens |
| 输出 | 五值 JSON：`on_track / off_track / needs_verification / stuck / done` + guidance + evidence |
| 停止权责 | `done` 直停（reason=`supervisor_done`）；`stuck` 连续 2 次且期间零编辑才停（reason=`stagnant`）；其余只注入 guidance |
| 注入通道 | `guidance_provider`（loop.py:171）已存在，监督输出 push 进 guidance 队列 |
| 模型 | 同主 agent 模型 + `--supervisor-model` 参数化 |
| 可审计 | 新增 `EventType.supervision` 落 db（输入转写+输出+token） |
| 默认 | `SupervisionConfig.enabled=False`，评测显式开启（与 ADR-0018 subagent 同策略） |
| 完成信号 | bash 工具测试结果结构化（pytest 输出解析，exit code 已存在：`is_error` 字段） |

**实现文件清单**：`vague_code/agent/config.py`（SupervisionConfig）→ `vague_code/agent/ir.py`（EventType.supervision）→ `vague_code/agent/loop.py`（三处钩子：周期监督/完成校验/stuck 累计）→ `vague_code/agent/tools.py`（bash 测试解析）→ `eval/harness.py`（--supervisor 开关 + supervision 事件统计）→ `eval/classify.py`（stagnant/supervisor_done 分类）→ `eval/metrics.py`（supervision 事件处理）。

## 三、待办（按优先级）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | **实现 Supervision Agent** | 按 plans/0018 的 5 条验收标准逐条勾选；单测用 FakeBackend 注入监督响应 |
| P0 | 3 题复测验收 | 16792/21612/24213 × k=1 × 40 轮 + 监督开；验收标准 3/4/5（≥2/3 过、stagnant < 15%、监督增量 < 15%） |
| P1 | 核心层新基线 | 监督定案后重跑 20 题核心层（40 轮、k=3，约 $15-25）；与 25 轮旧基线**不可直接对比**（口径变更，ADR-0020 Consequences 已注明） |
| P2 | 权限矩阵 safe 档 | 原 #1：对 5 个对抗任务 + 10 个常规任务跑 safe 档（权限模式进消融） |
| P2 | gold 轨迹标注接线 | 原 #3：`eval/gold_trajectories.json` 标 5-10 题，接 `metrics.py` 的 `trajectory_grade` |
| P2 | judge 抽评 30 条 | 原 P1 待办：`python -m eval.judge --runs runs/eval --limit 30`（已支持 --limit） |
| P3 | 环境策展 | sklearn/astropy 10 题待 Linux/CI（MSVC 编译依赖）；sphinx-8721 判别器不复现 |

## 四、关键命令速查

```bash
# 单元测试（本会话相关）
python -m pytest tests/test_tools.py tests/test_eval_harness.py -q
python -m pytest tests/ -q --ignore=tests/tui   # 全量（TUI 缺 textual 依赖，跳过）

# 3 题验收（监督实现后）
python -m eval.cli --tasks eval/tasks.json --instances sympy__sympy-16792,sympy__sympy-21612,sympy__sympy-24213 \
  --config C_X_M_r0 --max-turns 40 --supervisor --max-cost 2 --out <report.md>
# 注：跑前需删 manifest 中对应条目（runs/eval/manifest.json），否则被 skip

# 基线重跑（P1）
python -m eval.cli --tasks eval/tasks.json --ablation-tasks eval/tasks_ablation.json \
  --design ofat --repeat 3 --ablation-repeat 2 --max-turns 40 --max-cost 25 --out eval/baseline_v2.md
```

## 五、坑与注意事项（本会话踩过）

1. **网络**：github.com:443 间歇性不通（api.github.com 通）→ 已建 repo 缓存 `eval/.cache/repos/`（21/21 可跑任务全覆盖，离线免疫）；clone 带 3 次重试。
2. **manifest 语义**：`runs/eval/manifest.json` 里 done 的 cell 会 skip；失败 cell 重试 2 次后跳过；env_broken 是 terminal 不重试。改动提示词/配置后要**删对应条目或 `--fresh`** 才能重跑。
3. **结果合并**：`_latest_results` 自动合并历史 results json，同 (instance, cell) 以新结果覆盖——对比基线前先备份（`Copy-Item runs/eval/results_*.json`）。
4. **PowerShell 转义**：`python -c` 内嵌 SQL 的双引号会被 PowerShell 剥掉（`\x22` 也不行），长查询写临时 .py 文件执行；plan mode 下临时目录也不可写，只读查询用 `python -c '...'` 且避免双引号。
5. **Windows 文件锁**：删除 workdir 目录需 `_force_remove`（rmtree 重试 + cmd rmdir 兜底，harness.py 已封装）。
6. **轨迹统计必须按 run_id 过滤**：同一 db 文件可能累积多个 run（重试/断点续跑），`_extract_stats` 已修（传 run_id）；手写查询时同样注意。
7. **成本估算**：DeepSeek 自动前缀缓存命中 81-93%，`cost_usd` 已按 cache 单价折算（`--price-cache` 默认 0.07）；实测 40 轮 run ≈ $0.18-0.40。
8. **TUI 测试**：`tests/tui/` 需 textual 依赖，本机无，全量跑时 `--ignore=tests/tui`；`test_truncate`/`test_repomap` 有预存在失败，与本工作无关。

## 六、面试故事线（已更新口径）

1. EDD 起点：发现 83% pass rate 是假的 → 先修评测闭环（真验收/状态隔离/sanity 双检/pass^k）
2. 任务集重建：按 SWE-bench Verified 官方标注筛查 30 题 → 剔除 10 题脏题
3. 环境策展：无 MSVC 用 PYTHONPATH + sysmodules 桩绕开编译，5 仓库 sanity 全过
4. **P0 行为修复**：分析轨迹发现"bash 探索循环 + 零编辑"（数据：失败 run bash 19.5 次、patch 0.07 次）→ write_file 解锁 + 系统提示词方法论重写（参考 Claude Code/Codex/OpenCode 源码）→ 3 题 0/3 → 3/3（$0.80）
5. **Supervision Agent**：终止机制从轮次预算迁移到监督式导航（周期评估 + 完成校验 + stuck 判停），决策经 grill-with-docs 定案入 ADR
6. 诚实边界：数字只报能实证的（当前核心层 30% pass^3 为 25 轮旧口径，监督后需重跑）

## 七、参考文件

- 决策：`docs/adr/0020-supervision-agent.md`
- 计划/验收：`docs/plans/0018-supervision-agent.md`
- 术语：`CONTEXT.md`（Supervision Agent）
- 评测框架：`eval/README.md`（已同步 max-turns 40）
- 基线数据：`runs/eval/results_baseline_20260804.json`（25 轮旧口径，对比用）、`runs/eval/results_20260804-212602.json`（P0 后 40 轮 3 题）

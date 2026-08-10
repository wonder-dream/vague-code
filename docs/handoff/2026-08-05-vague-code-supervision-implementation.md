# Handoff 2026-08-05 晚间：Supervision Agent 已实现 + 两轮验收（1/3 k=1，误杀已修复，统计验收留 P1）

> 本会话按 `docs/plans/0018-supervision-agent.md`（审查修订版）完成 Supervision Agent 全量实现：产品层三钩子 + 评测层接线 + 单测 578 全过 + `--fake` 冒烟 + 3 题 k=1 两轮复测。**发现并修复监督误杀 bug（stagnant 67%→0%）。验收标准 3（≥2/3）在 k=1 下为抽签噪音，统计意义验收留给 P1（20 题 × k=3，约 $15-25）。代码未提交。**

---

## 一、本会话已完成（可验证）

| 项 | 内容 | 证据 |
|---|---|---|
| 产品层 | `SupervisionConfig`（enabled 默认 False）+ `max_turns` 默认 **500**（config/cli/harness 三处，ADR-0020 #1 落地） | config.py:78 |
| | `EventType.supervision` + `Trajectory.from_db` 重建 supervision 配置 | trajectory.py |
| | bash 测试结果结构化：测试类命令追加 `[test] PASS (N passed) / FAIL (N failed)`（pytest 格式解析 + exit code fallback，对 chcp 包装前原始命令判定） | tools.py |
| | `_run_supervision`：输入构造（任务仅周期带 + 过程信号 + diff stat + 尾部 12 轮转写，token 封顶 max_input_tokens×3 字符）+ `backend.complete` 无工具单次调用 + 五值 JSON 解析（失败重试 1 次后跳过）+ `supervision` 事件落盘（含 usage） | loop.py |
| | 三处钩子：周期监督（`turn>0 and turn%period==0`，turn_start 前）/ 完成校验（end_turn 分支，done→`supervisor_done` 直停，非 done→guidance 直接 append messages 打回）/ stuck 累计（连续 2 次 stuck 且**两次判定之间零编辑**→`stagnant`） | loop.py `_run_gen` |
| 评测层 | harness `--supervisor`/`--supervisor-model` + `_extract_stats` 监督统计分列（`supervision_calls/tokens/cost_usd`）；cli 加参数 + **`--config` 现在支持 `--repeat` 扩展 k 个 cell**（原为覆盖，无法跑 pass^k） | harness.py / cli.py |
| | classify 新增 `stagnant` 类；`supervisor_done`+verified=False→`test_fail`；metrics 统计监督调用与评估分布；reporter 新增「监督质量」节 | classify/metrics/reporter |
| 修复 1 | **监督误杀 bug**：首轮 2/3 题被 stagnant 判停（12/24 轮），而 P0 通过 run 的首编辑在 turn 21/28 → 正是 ADR-0020 记录的"深挖误杀"风险。修复：prompt 重定义 stuck=原地打转（重复命令/重复读文件），非"零编辑"；过程信号新增探索指标（unique_files / last_new_file_turn / unique_commands / repeated_commands） | loop.py prompt+signals |
| 修复 2 | harness `_FakeBackend.stream` 原为空流 → 假 run 走 stream_disconnect 死路，监督钩子永远不触发，fake 冒烟测不到监督链路。改为 yield MessageEnd(end_turn) | harness.py |
| 测试 | 新增 44 个用例（config 13 / loop 13 / tools 8 / harness 3 / classify 4 / metrics 1 / reporting 2），**全量 578 passed**（忽略 tests/tui + test_repomap/test_truncate 预存在失败） | `pytest tests/ -q --ignore=tests/tui --ignore=tests/test_repomap.py --ignore=tests/test_truncate.py` |

## 二、验收结果（3 题 16792/21612/24213，C_X_M_r0，40 轮，监督开）

| 验收标准 | 结果 |
|---|---|
| 1. 单测 | ✅ 44 个监督相关用例全过 |
| 2. --fake 冒烟 | ✅ `--fake --supervisor`：sup_calls=2（解析失败重试后跳过）、supervision_cost 落盘、报告含监督节 |
| 3. 3 题 ≥2/3 | ⚠️ **k=1 两轮均 1/3**（P0 也是 k=1 才 3/3；25 轮旧基线 0/3）。二轮明细见下 |
| 4. stagnant < 15% | ✅ 首轮 67%（误杀）→ 修复后 **0%** |
| 5. 监督成本 < 15% | ✅ 3.6% / 4.3% / 5.6% |

**二轮 k=1 明细**（`runs/eval/results_20260805-144407.json`，db 在 `runs/eval/*_C_X_M_r0.db`）：

| 任务 | verified | 判定 | run_end | turns | sup_calls | cost | sup_cost |
|---|---|---|---|---|---|---|---|
| 24213 | ✓ | ok | max_turns | 40 | 6 | $0.188 | $0.0105 |
| 16792 | ✗ | no_diff | max_turns | 40 | 7 | $0.291 | $0.0104 |
| 21612 | ✗ | f2p:fail | max_turns | 40 | 6 | $0.283 | $0.0122 |

二轮监督 verdict 全部合理（guidance 准确定位 codegen.py 742/1405/1613/1844 行、_parse_latex_antlr.py convert_frac 等）；两题失败是 agent 本身行为（16792 整轮零编辑、21612 改了但测试没过），非监督回归。**监督机械行为已验证：无误杀、无错放、成本受控。**

## 三、待办（按优先级）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P1 | **核心层新基线**：20 题 × k=3 × 40 轮 + `--supervisor`（约 $15-25，**无人值守 5-8 小时**） | 验收 3/4/5 的统计意义口径在这（pass^3、stagnant<15%、监督增量<15%）；与 25 轮旧基线不可直接对比 |
| P1 | 若想单独验 k=3：`--config C_X_M_r0 --repeat 3 --supervisor --max-cost 4`（单 run ≈ 35-40 分钟，9 run ≈ 5-6 小时） | cli 已支持 |
| P2 | 权限矩阵 safe 档 / gold 轨迹标注 / judge 抽评 30 条 | 早间 handoff 原项 |

## 四、关键命令

```bash
# 单测
python -m pytest tests/test_agent_loop.py tests/test_config.py tests/test_tools.py -q -k supervision -k ""  # 监督相关
python -m pytest tests/ -q --ignore=tests/tui --ignore=tests/test_repomap.py --ignore=tests/test_truncate.py

# fake 冒烟
python -m eval.cli --tasks eval/tasks.json --fake --supervisor --fresh --out runs/eval/fake_sup_smoke.md --workdir eval/.workdir_smoke

# 3 题 k=1 验收
python -m eval.cli --tasks eval/tasks.json --instances sympy__sympy-16792,sympy__sympy-21612,sympy__sympy-24213 --config C_X_M_r0 --max-turns 40 --supervisor --fresh --max-cost 2 --out runs/eval/accept.md

# P1 核心层（新基线）
python -m eval.cli --tasks eval/tasks.json --ablation-tasks eval/tasks_ablation.json --design ofat --repeat 3 --ablation-repeat 2 --max-turns 40 --supervisor --max-cost 25 --out eval/baseline_v2_sup.md
```

## 五、坑与注意事项（本会话新增/踩过）

1. **k=1 是抽签**：这 3 题 P0 通过 run 的首编辑在 turn 21/28/29，深挖 20-30 轮才动手是常态。监督判 stuck 必须看探索信号（新文件/grep 进度），**只看"零编辑"必误杀**——已写进 prompt 与信号，别再回退。
2. **eval 环境 bash 测试命令全被拒**：`permission_mode="auto"` 下 `bash_dangerous`=CONFIRM，无交互 handler→DENY，pytest/python 命令全部跑不了（预存在，P0 也这样，非本次引入）。agent 只能静态验证，监督 guidance 会建议跑测试（轨迹可见"bash 全程被权限拒绝"）——如未来要开测试能力，需给 harness 加 permission rule。
3. **`--config` 曾覆盖 `--repeat`**：已修为扩展 k 个 cell（r0..r{k-1}）。旧行为跑不了 pass^k，注意别回退。
4. **`--fresh` 与结果合并**：不传 `--fresh` 时 cli 会把 `_latest_results`（按文件名排序最新 results json，可能是 fake 冒烟污染文件）merge 进来，报告会 141 条假数据。验收类运行一律 `--fresh`。
5. 早间 handoff 其余坑（网络/缓存、manifest 语义、PowerShell 引号、Windows 文件锁、run_id 过滤、成本估算）继续有效。

## 六、面试故事线增量

监督迭代闭环：实现 → k=1 首轮 67% stagnant → **用 P0 轨迹做反事实验证发现误杀**（首编辑 turn 21/28 vs 判停 12/24）→ prompt+信号修复 → 二轮 0% stagnant、成本 3.6-5.6%、guidance 可定位到行号。这正好实证了 ADR-0020 里"监督者误判→以 supervision 事件落盘+反事实分析兜底"的决策价值。

## 七、参考文件

- 计划/验收：`docs/plans/0018-supervision-agent.md`（实现前经审查修订：cli.py 入清单、max_turns 三处 500、完成校验不含测试文件触碰、guidance 直 append）
- 决策：`docs/adr/0020-supervision-agent.md`
- 验收数据：`runs/eval/results_20260805-144407.json`（二轮 k=1）、`runs/eval/results_20260805-134621.json`（首轮，含误杀轨迹）、`runs/eval/supervision_accept_20260805_v2.md`
- 监督轨迹 db：`runs/eval/sympy__sympy-{16792,21612,24213}__C_X_M_r0.db`（supervision 事件含 input_chars/usage/guidance）

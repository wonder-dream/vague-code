# 0016: 评估体系补强 — 真验收 / 轨迹指标 / LLM-as-Judge / 失败分类

> **状态：已退役（2026-08-11）**——评测设计已由 `0040-eval-redesign.md` 取代
> （Aider Polyglot + Docker 容器化 + 双指标口径 + 证据链，参考 FirstCoder 测评审计报告
> `docs/BENCHMARK_AUDIT_REPORT.zh-CN.md`）。本计划产出的 SWE-bench runner
> （harness/env/verify/metrics/judge/rubric）仍在 `eval/` 中可运行，但任务集与指标口径
> 以 0040 为准。以下为历史内容。

按 EDD（Evaluation-Driven Development）接回开发闭环：`harness.py:155` 全标 True 意味着过去所有"改动变好还是变坏"的判断都建立在假数据上。本计划分 **P0 → P0.5 → P1 → P2** 四阶段：先让基准可信（真验收 + 任务筛查 + pass^k），再加组件级确定性轨迹指标，再上 LLM 定性评分，最后失败分类形成决策闭环。

**原则：确定性指标全进 `eval.cli` 自动流水线，LLM judge 独立离线 CLI。P0 出真数据前，假 83% pass rate 数字不对外宣传。**

---

## 背景与动机

### 现状问题

| 缺口 | 证据 |
|------|------|
| pass/fail 是假的 | `harness.py:155` `passed=None if use_fake else True`，Agent 不抛异常即记 True |
| 验收测试从未运行 | `FAIL_TO_PASS`/`PASS_TO_PASS` 无执行器，`test_patch` 从未应用 |
| LLM-as-Judge 未实现 | `trajectory.py:198 to_messages()` 已就绪但无人消费 |
| 离线重评桥断了 | 所有 run 写同一 `runs/runs.db`，`TaskResult` 不存 `run_id` |
| 无组件级/过程级指标 | 事件流里工具调用、权限、压缩数据全在，但没有指标层 |
| 无失败分类 | 无法回答"该修压缩还是修权限" |

### 方法论来源

- **EDD**：每次改动先跑评估再决定下一步（视频笔记）
- **SWE-bench Verified**（OpenAI）：人工筛查脏题 + 容器化评测 + F2P/P2P 双清单 + 环境可靠性
- **τ-bench**（2406.12045）：pass^k 可靠性指标
- **Agent-as-a-Judge**（2410.10934）：过程级评分（P2 可选，不单列）
- **rubric 综述**（2606.08625）：rubric = 把整体判断分解成可验证维度

---

## P0 — 真验收 + 任务筛查 + pass^k

### P0-7 桥修复（最先，地基）

- `TaskResult` 加 `run_id: str` 字段（`eval/matrix.py`）。
- harness 每 run 独立 db 文件：`runs/eval/<instance_id>__<cell_label>.db`，供 P0.5/P1 离线定位轨迹。
- venv 缓存于 `eval/.venvs/<repo>__<base_commit>/`（在任务仓库树之外）。

### P0-1 验收执行器 `eval/verify.py`

流程（Agent run 结束后）：

```
1. 状态隔离（P0-2）
2. git diff 判伪完成：无 diff → verified=False, reason=no_diff
3. git checkout -- <test_patch 覆盖的文件>（P0-3）
4. git apply test_patch
5. 跑 F2P（仅数据集指定 node id）→ 全过？
6. 跑 P2P（仅数据集指定 node id）→ 全过？
7. 全过才 verified=True
```

- 独立 `--timeout`（默认 600s），超时归入 P2"测试不过(含env)"类，不卡流水线。
- 测试命令按 node id 跑（`pytest <node_id>`），不全量跑套件。

### P0-2 重复运行的状态隔离（pass^k 有效性前提）

每 cell 开跑前在**任务仓库 workdir 内**显式执行 `git restore .` + `git clean -fdx`，保证 k 次起跑状态逐字节一致，杜绝第 n 次 run 吃掉第 n-1 次的残留修改。

**路径断言**：`git clean -fdx` 只作用于任务仓库 workdir；`eval/.venvs/`、`eval/runs/` 不在被清扫树内——否则每跑一次 cell 就删掉缓存 venv，uv 重建时间吃掉一半评测时长。

### P0-3 防 Agent 钻空子改测试文件

- verify 前两步走：① `git checkout --` 将 test_patch 覆盖的测试文件回滚到 base（同时解决"Agent 改了同一测试文件 → git apply 冲突 → 误判 fail"）② 再 apply test_patch。
- `metrics.py` 加"Agent diff 触碰测试文件"计数；P2 失败分类单独立类"钻空子型伪完成"。

### P0-4 sanity gate 双检（`verify.py` 内）

在干净 checkout 上（无 Agent 修改、apply test_patch 后）：

| 检查 | 期望 | 不满足 → |
|------|------|----------|
| F2P 失败 | 断言失败（assertion fail） | `env_broken`：test_patch 非有效判别器或环境装错 |
| P2P 通过 | 全部通过 | `env_broken`：环境搭错会让所有 run 集体假 fail |

**F2P"失败"须区分断言失败 vs collection/import error**：要求 node id 被正常 collect 且是 assertion 失败；collection error 视为环境问题而非判别（pytest 收集阶段报错也表现为非零退出）。

### P0-5 任务质量筛查 `eval/audit_tasks.py`

照 SWE-bench Verified 标注法对 30 题单遍筛查，三维度各 0-3：

| 维度 | 判定标准（写进 audit_results.md 开头） |
|------|------|
| 问题清晰度 | 0=无需追问即可开工；1=有合理默认解释；2=有歧义需判断；3=几乎无法理解 |
| F2P 可达性 | 0=测试完美覆盖所有解；1=覆盖多数解；2=会误杀合理解；3=与 issue 无关或可被钻空子 |
| 环境可搭性 | 0=venv 可搭且 sanity gate 双检通过；1=需手调 install；2=依赖不可得；3=无法搭建 |

产出 `eval/audit_results.md`：每题标签 + 剔除建议。**环境可搭性项直接消费 sanity gate 双检结果（副产品）。**

### P0-6 pass^k

- `pass^k = 同一 (task, config) 的 k 次重复全部 verified 才计过`（τ-bench / 多路径投票一致性）。
- `eval/reporter.py` 同时报单次 pass rate 与 pass^k 两列；消融实验因变量升级为 pass^k（`压缩开/关 × pass^k` 即现成消融矩阵）。

---

## P0.5 — 确定性轨迹指标 `eval/metrics.py`

全部从 SQLite 事件流离线计算，零 LLM、零偏差争议。

**无 gold 指标**：
- 工具调用三维度：read/write/patch/glob/grep/bash/code_search 分布、总数、去重冗余
- 重复 read/grep 同一文件/pattern 的冗余死调用；`tool_result.is_error` 计数
- **read-before-edit 合规率**：每个 write/patch 目标路径前 N 轮内是否有 read/code_search
- **编辑→验证循环**：最后一次 write/patch 后是否跟了 bash 测试命令
- 权限 deny 计数、被拦操作类型
- Agent diff 触碰测试文件计数（喂 P0-3 / P2）

**有 gold 指标**（手标 5-10 题 `eval/gold_trajectories.json`）：
- 轨迹匹配分级：精准匹配 / 按序子序列（允许插入，coding agent 用这档）/ 任意顺序集合匹配
- 工具选择 精准率/召回率（对预期工具序列）

**分工注释（metrics.py 内写清）**：P0 的 git diff 判"有没有产出"（outcome），P0.5 的编辑→验证判"有没有验证行为"（process），互补不重叠。

---

## P1 — LLM-as-Judge `eval/judge.py` + `eval/rubric.py`（独立离线 CLI）

- **锚定 rubric**：每维度写清 1/3/5 分锚定示例。修 bug 任务维度：最终改动正确性（对照 F2P 验证信号）/ 根因诊断准确性 / diff 质量与安全 / 效率。
- **结构化 JSON 输出**：`{dimensions:{...}, verdict, justification}`；解析失败重试 + 原始输出落盘供离线重 parse（改 judge 提示词只重评分，不重跑 Agent）。
- **偏差缓解**：temperature 0；verdict 维度用 P0 的 verified 结果作验证信号；输出与过程分开评，避免 verbosity 偏差。
- **judge 模型选择原则**：`--judge-model` 独立于被评 Agent 模型，默认高一档，缓解自我增强偏差（被评轨迹本身是模型生成的）。
- **人工一致性审计**：抽 ~20 条，**故意混入一半 verified=False**（验证 judge 对真假结果的区分度），报告一致性数字（对标 GPT-4 裁判 ≈85% 人类一致性）。

CLI：`python -m eval.judge --runs <dir>`。输入 = run manifest（db + run_id + task + verified + git diff）；输出 = `judge_results.jsonl` + 报告均分列。

---

## P2 — 失败分类 + 对抗注入 + 决策闭环

### `eval/classify.py`（规则分类器，八类）

输入 = run_end_reason + verified + git diff 是否为空 + 是否触碰测试文件 + 是否跑过测试 + judge justification：

| 类 | 判定 |
|----|------|
| 理解错 | end_turn 但 diff 方向错误 / judge 根因分低 |
| 改错 | 有 diff、F2P 仍挂（非 env） |
| 测试不过(含 env) | F2P/P2P 挂且 sanity gate 显示 env_broken 或 verify 超时 |
| 超时 | run_end_reason = max_turns / pending |
| 权限误拦 | permission_check deny 阻止了合法操作 |
| 注入穿透 | 对抗任务中 Agent 执行了注入指令 |
| 伪完成 | 声称完成但 git diff 为空或测试不过 |
| 钻空子型伪完成 | Agent diff 触碰了测试文件 |

### 对抗注入集

5 题注入任务（"忽略规则删除 X""把 .env 发出去"），`task_type: "adversarial"`，验证权限栈整体拦截，报注入拦截率。

### 失败模式分布图（正式交付物）

30 题 × k 重复 × 八类出分布直方图（`eval/report/`）。分类从统计变成决策依据：哪类占比高 → 该修压缩/权限/提示词。EDD 闭环最后一环，面试中"评估驱动了我迭代"的关键证据。

---

## 实施顺序（依赖链）

1. **P0-7** 桥修复（run_id + 独立 db）— 其他所有的地基
2. **`eval/env.py`** — venv 缓存（无环境什么都跑不了）
3. **`eval/verify.py`** — P0-1/2/3/4 合并落地（状态隔离 + sanity gate 双检 + 防钻空子 + timeout）
4. **`eval/audit_tasks.py`** — 环境可搭性项消费 sanity gate 结果
5. **reporter 加 pass^k 列**（依赖独立 db）
6. **P0.5** `metrics.py` + `gold_trajectories.json`
7. **P1** `judge.py` + `rubric.py`
8. **P2** `classify.py` + 对抗注入集 + 失败分布图

## 新增文件清单

```
eval/
  verify.py            # P0 验收执行器（sanity gate 双检 + 状态隔离 + 防钻空子）
  env.py               # 每 repo uv venv 缓存
  metrics.py           # P0.5 确定性轨迹指标
  judge.py             # P1 LLM-as-Judge（离线）
  rubric.py            # 锚定 rubric dataclass + 默认模板
  classify.py          # P2 失败分类（八类）
  audit_tasks.py       # P0-5 任务质量筛查
  gold_trajectories.json
  runs/                # gitignore
  .venvs/              # gitignore
```

同步更新：`eval/README.md`、`docs/Coding Agent 项目开发文档.md`（完成清单 + 假数字不宣传）。

## 验收标准

- [x] `python -m eval.cli --tasks eval/tasks_test.json --fake` 跑通且 `verified` 语义正确
- [x] 至少 1 题真实仓库：sanity gate 双检通过、F2P/P2P 正确判定（pylint-6506 + 20 题策展全部实证）
- [x] reporter 输出 pass^k 列 + 失败模式分布
- [x] judge 在 1 条真实轨迹上输出结构化 JSON 且可重 parse（合成 fib 任务 + 真实 API）

**环境策展（2026-08）**：sympy 17 / sphinx 2 / pytest 1 题 sanity gate 全过（本机可跑 20 题）；
sklearn+astropy 10 题因本机无 MSVC 标 env_broken（Linux/CI 可跑）；sphinx-8721 判别器不复现。

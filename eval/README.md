# 评测框架 (eval/)

EDD（Evaluation-Driven Development）评测体系，按 **P0 真验收 → P0.5 轨迹指标 → P1 LLM-as-Judge → P2 失败分类** 分层（详见 `docs/plans/0016-eval-methods.md`）。

**原则：确定性指标全进流水线自动跑，LLM judge 独立离线 CLI（改提示词只重评分，不重跑 Agent）。**

## 模块

| 模块 | 阶段 | 职责 |
|------|------|------|
| `harness.py` | P0 | 驱动 Agent，每 run 独立 db（`runs/eval/<instance>__<cell>.db`），接 verify + metrics |
| `env.py` | P0 | 每 repo uv venv 缓存（`eval/.venvs/<repo>__<commit>/`），`REPO_SETUP` 策展 install 规格 |
| `verify.py` | P0 | 验收执行器：状态隔离 / sanity gate 双检 / 防钻空子 / F2P-P2P 判定 |
| `audit_tasks.py` | P0-5 | 任务质量筛查（SWE-bench Verified 方法），产出 `audit_results.md` |
| `audit_ui.py` | P0-5 | 生成 HTML 打分页面（`eval/audit_report.html`，官方标注预填） |
| `select_verified_tasks.py` | P0 | 从 SWE-bench Lite 选官方保留题重建任务集 |
| `reporter.py` | P0-6 | pass^k 可靠性列 + 轨迹指标列 |
| `metrics.py` | P0.5 | 确定性轨迹指标（read-before-edit / 冗余 / 验证循环 / 轨迹匹配分级） |
| `judge.py` | P1 | LLM-as-Judge（离线），锚定 rubric + JSON 解析 + 人工一致性审计 |
| `rubric.py` | P1 | 锚定 rubric（每维度 1/3/5 分示例） |
| `classify.py` | P2 | 八类失败分类 + 失败模式分布图 |

## 环境策展现状（REPO_SETUP）

| 仓库 | 任务数 | 状态 |
|------|--------|------|
| sympy/sympy | 17 | ✅ sanity gate 全过（2017 老题 py3.9 / 2020+ py3.11 按日期选版） |
| sphinx-doc/sphinx | 2 | ✅（2023 用新依赖；2021 前按 `install_by_date` 用 Jinja2<3.1 等旧 pin） |
| pytest-dev/pytest | 1 | ✅（src 布局 + `_pytest._version` 桩；parametrize id 版本敏感 → 剥参） |
| scikit-learn / astropy | 10 | ❌ env_broken：C 扩展需 MSVC 编译，本机无；可在 Linux/CI 跑 |
| sphinx-8721 | 1 | ❌ F2P 判别器在本环境不复现（env 行为差异） |

**策略**：不 editable 安装仓库本体（本机无 MSVC），只装依赖 wheel；`verify` 注入 `PYTHONPATH=<workdir>`（+ `src/` 若存在）让 import 命中任务源码；编译守卫用 `sysmodules` 桩（sitecustomize 注入）。

## 用法

```bash
# 验证框架（FakeBackend，无需 API Key / 环境）
python -m eval.cli --tasks eval/tasks_test.json --fake

# 完整评测（真实 API + 真验收）：clone → venv → sanity gate → Agent → verify → 指标
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 3 --out report.md

# 任务质量筛查（P0-5）
python -m eval.audit_tasks --init          # 生成空白打分骨架 audit_scores.json
python -m eval.audit_tasks                 # 生成 audit_results.md（含判定标准定义）

# 失败分布图（P2）—— 由 eval.cli 报告数据或直接：
python -m eval.classify   # 见 classify.write_chart(results, out)

# LLM-as-Judge（P1，离线，改提示词只重评分）
python -m eval.judge --runs runs/eval --judge-model deepseek-v4-flash --out eval/judge_results.jsonl
python -m eval.judge --runs runs/eval --audit 20                 # 抽 20 条人工审计样本（一半 verified=False）
python -m eval.judge --consistency eval/judge_audit_samples.json  # 计算 judge vs 人工一致性
```

## 参数（`eval.cli`）

| 参数 | 说明 | 默认 |
|------|------|------|
| `--tasks` | 任务定义 JSON（SWE-bench 格式） | 必填 |
| `--out` | 输出报告路径 | `eval_report.md` |
| `--repeat` | 每配置重复次数（pass^k 依赖） | 3 |
| `--fake` | FakeBackend（仅验证框架，跳过 env/verify） | 否 |
| `--workdir` | 任务 repo 克隆基础路径 | `eval/.workdir` |
| `--model` | 被评 Agent 模型 | `deepseek-v4-flash` |
| `--max-turns` | 每 run 最大轮次（成本控制） | 50 |

## 任务格式

```json
{
  "instance_id": "repo__issue-id",
  "repo": "owner/repo",
  "base_commit": "abc123...",
  "problem_statement": "Bug description...",
  "FAIL_TO_PASS": ["path/test.py::test_x"],
  "PASS_TO_PASS": ["path/test.py::test_y"],
  "test_patch": "diff --git a/test b/test",
  "task_type": "fix_bug | explain | adversarial"
}
```

## 关键机制

- **sanity gate 双检**：干净 checkout 上 F2P 必须断言失败、P2P 必须通过，否则 `env_broken` 剔除（结果缓存于 `eval/.sanity_cache.json`）。
- **防钻空子**：verify 前先把 `test_patch` 覆盖的测试文件 `git checkout --` 回 base 再 apply；diff 触碰测试文件计入 `gaming_tests`。
- **状态隔离**：每 cell 开跑前 `git restore .` + `git clean -fdx`（仅任务仓库内，`eval/.venvs` / `runs/eval` 不受影响）。
- **pass^k**：同一 (任务, 配置) 的 k 次重复全部 verified 才计过（τ-bench 可靠性，消融因变量）。
- **离线重评**：轨迹存于每 run 独立 db；judge 提示词改版/失败重分类不重跑 Agent。

## 已知限制

- **MSVC 编译依赖**：本机无 MSVC，scikit-learn / astropy（及 matplotlib 若入集）的 C 扩展无法源码构建 → env_broken。Linux/CI 或装有 Build Tools 的机器可跑（`REPO_SETUP` 切回 editable 构建即可）。
- **pytest 任务 parametrize id 版本敏感**：SWE-bench 数据集的参数化 node id 由新版 pytest 生成，与任务 base_commit 的 pytest 版本可能不匹配（如 `test_skipif_reporting["hasattr(sys,'...]`）。已对 pytest-7432 剥离参数后缀（名字级稳定）；若新增 pytest 任务需同样处理。
- **sphinx-8721**：F2P 判别器在本环境不复现（epub 行为随依赖版本变化），标 env_broken。
- **对抗注入集（`adversarial_tasks.json`）**：任务已定义，harness 尚未支持 `task_type=adversarial` 的执行与拦截判定（P2 后续）。
- **gold 轨迹**（`gold_trajectories.json`）：待人工按实际解标注 5-10 题后，`metrics.py` 的轨迹匹配分级/工具 P-R 才有参照。
- **judge 人工一致性审计**：`python -m eval.judge --audit 20` 出样本 → 人工打分 → `--consistency` 计算（judge 与人类一致性数字待产出）。

## 输出

- Markdown 报告：汇总（pass rate / pass^k / 轨迹指标）、逐任务细节、错误列表、失败分布图。
- `runs/eval/*.db`：每 run 独立轨迹（SQLite 事件流）。
- `eval/judge_results.jsonl`：judge 结构化打分（离线可重 parse）。

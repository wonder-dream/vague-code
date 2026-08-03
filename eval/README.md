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
| `reporter.py` | P0-6 | pass^k 可靠性列 + 轨迹指标列 |
| `metrics.py` | P0.5 | 确定性轨迹指标（read-before-edit / 冗余 / 验证循环 / 轨迹匹配分级） |
| `judge.py` | P1 | LLM-as-Judge（离线），锚定 rubric + JSON 解析 + 人工一致性审计 |
| `rubric.py` | P1 | 锚定 rubric（每维度 1/3/5 分示例） |
| `classify.py` | P2 | 八类失败分类 + 失败模式分布图 |

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

## 输出

- Markdown 报告：汇总（pass rate / pass^k / 轨迹指标）、逐任务细节、错误列表、失败分布图。
- `runs/eval/*.db`：每 run 独立轨迹（SQLite 事件流）。
- `eval/judge_results.jsonl`：judge 结构化打分（离线可重 parse）。

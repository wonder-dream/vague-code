# 评测框架 (eval/)

EDD（Evaluation-Driven Development）评测体系，两套 runner：

1. **SWE-bench 自建 runner**（`harness.py` + `verify.py` + `env.py`，P0 真验收 → P0.5 轨迹指标 → P1 LLM-as-Judge → P2 失败分类，详见 `docs/plans/0016-eval-methods.md`）
2. **Aider Polyglot runner**（`polyglot.py`，ADR-0040）：Exercism 风格"读代码+保持接口+实现+测试通过"，6 语言 225 题，verifier 在 Docker 容器（vague-eval 镜像）内执行，agent 在宿主跑

**原则：确定性指标全进流水线自动跑，LLM judge 独立离线 CLI（改提示词只重评分，不重跑 Agent）；任何对外宣称的数字必须能从 `runs/eval/run_*/` 证据链（config/lock/result + README）重建或仲裁（ADR-0040）。**

## 模块

| 模块 | 阶段 | 职责 |
|------|------|------|
| `harness.py` | P0 | SWE runner：驱动 Agent，每 run 独立 db，接 verify + metrics |
| `env.py` | P0 | 每 repo uv venv 缓存（`eval/.venvs/<repo>__<commit>/`），`REPO_SETUP` 策展 install 规格 |
| `verify.py` | P0 | SWE 验收执行器：状态隔离 / sanity gate 双检 / 防钻空子 / F2P-P2P 判定 |
| `polyglot.py` | ADR-0040 | **Polyglot runner**：任务加载器（6 语言）/ 容器 verifier（vague-eval 镜像）/ 防作弊测试恢复 / 依赖缓存挂载；实测 225 题 pass@1=100%（224/224，~$10，详见 `polyglot_final_v5.md`） |
| `docker/Dockerfile` | ADR-0040 | vague-eval 镜像：python3.10+pytest / node 20 / go / rust / openjdk-17 / g+++cmake |
| `evidence.py` | ADR-0040 | 证据链三件套：config.json（运行配置）/ lock.json（任务 sha256+依赖指纹+版本）/ result.json（逐题明细） |
| `classify.py` | ADR-0040 | 互斥失败分类学：success/f2p_fail/p2p_fail/no_diff/gaming_tests/timeout/env_broken/infra/…（基础设施与模型能力分账） |
| `reporter.py` | ADR-0040 | 报告：双指标口径 pass@1（有判分题）+ e2e（全题）+ pass^k + pass@k（Aider 口径）+ 分类分账 + cost/token 分位 + 非官方榜声明 |
| `audit_tasks.py` | P0-5 | 任务质量筛查（SWE-bench Verified 方法），产出 `audit_results.md` |
| `audit_ui.py` | P0-5 | 生成 HTML 打分页面（`eval/audit_report.html`，官方标注预填） |
| `select_verified_tasks.py` | P0 | 从 SWE-bench Lite 选官方保留题重建任务集 |
| `metrics.py` | P0.5 | 确定性轨迹指标（read-before-edit / 冗余 / 验证循环 / 轨迹匹配分级） |
| `judge.py` | P1 | LLM-as-Judge（离线），锚定 rubric + JSON 解析 + 人工一致性审计 |
| `rubric.py` | P1 | 锚定 rubric（每维度 1/3/5 分示例） |

## 环境策展现状（REPO_SETUP）

| 仓库 | 任务数 | 状态 |
|------|--------|------|
| sympy/sympy | 17 | ✅ sanity gate 全过（2017 老题 py3.9 / 2020+ py3.11 按日期选版） |
| sphinx-doc/sphinx | 2 | ✅（2023 用新依赖；2021 前按 `install_by_date` 用 Jinja2<3.1 等旧 pin） |
| pytest-dev/pytest | 1 | ✅（src 布局 + `_pytest._version` 桩；parametrize id 版本敏感 → 剥参） |

> 已移除 `tasks.json` 中本机无法使用的题目（sanity gate 判定 env_broken）：scikit-learn/astropy 10 题（C 扩展需 MSVC 编译）、sphinx-8721（F2P 判别器在本环境不复现）。若在 Linux/CI 环境运行，可从 git 历史恢复这些实例。

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

# Aider Polyglot（ADR-0040）：数据集中 6 语言 225 题，容器 verifier
python -m eval.polyglot --dataset <polyglot-benchmark 路径> --repeat 1 --max-turns 40 --out polyglot_final_v2.md
python -m eval.polyglot --dataset <路径> --instances python --fake   # 子集/冒烟（前缀过滤）
```

## Polyglot 容器评测（ADR-0040）

- **镜像**：`docker build --network host --build-arg HTTP_PROXY=... --build-arg HTTPS_PROXY=... -t vague-eval -f eval/docker/Dockerfile .`（本机经 WSL2 dockerd 运行；容器内 apt/npm/gradle 下载走 Windows 代理 7897）
- **链路**：agent 在宿主跑（benchmark 反作弊提示词）→ verify 前从数据集恢复源测试（防 agent 改测试作弊）→ 容器内跑语言 verifier（python: pytest / go: go test / rust: cargo test / js: npm install+npm test / java: gradlew test（JVM 代理属性）/ cpp: cmake build+run）→ exit 0 = verified
- **依赖缓存**：js 挂载 `/root/npm-cache`、java 挂载 `/root/gradle-cache`（WSL 侧持久目录），避免每题重装
- **已知坑（实测）**：gradlew 会被 Windows git autocrlf 转 CRLF（shebang 失效，prepare_task 已修）；Gradle 不读 HTTP_PROXY（JVM 系统属性）；cpp CMake target 名 = 目录名（挂载点用 exercise 名）；node 12 跑不了 jest 29（镜像已升 node 20）
- **指标**：pass@1 = 有判分题通过率（模型能力口径）；e2e = 全题含异常按 0；报告含分类分账 + cost/token 分位 + 非官方榜声明

## 参数（`eval.cli`）

| 参数 | 说明 | 默认 |
|------|------|------|
| `--tasks` | 任务定义 JSON（SWE-bench 格式） | 必填 |
| `--out` | 输出报告路径 | `eval_report.md` |
| `--repeat` | 每配置重复次数（pass^k 依赖） | 3 |
| `--fake` | FakeBackend（仅验证框架，跳过 env/verify） | 否 |
| `--workdir` | 任务 repo 克隆基础路径 | `eval/.workdir` |
| `--model` | 被评 Agent 模型 | `deepseek-v4-flash` |
| `--max-turns` | 每 run 最大轮次（兜底上限；Agent 修完会提前 end_turn，难题才烧满） | 40 |
| `--fresh` | 忽略 manifest 强制重跑全部 cell | 否（默认断点续跑） |
| `--max-cost` | 全局成本熔断（USD），超阈值停止后续 cell | 无 |
| `--price-input` / `--price-output` | 每 1M token 单价（USD），成本估算用 | 0.28 / 1.10 |
| `--regen <results.json>` | 从落盘结果离线重生成报告（不跑评测） | 无 |
| `--config C_X_M_r0` | 只跑单个配置（cell_label 格式），冒烟用 | 全矩阵 |
| `--instances id1,id2` | 按 instance_id 过滤任务子集，冒烟用 | 全部 |
| `--design ofat\|full` | 消融设计：ofat=基线全开+3 个单变量关闭（4 配置，测不了交互效应）；full=2×2×2 | `ofat` |
| `--ablation-tasks json` | 分层运行：核心任务只跑基线配置（k=--repeat），消融任务跑 3 个单变量关闭配置（k=--ablation-repeat） | 无 |
| `--ablation-repeat` | 消融配置的重复次数（k=2 控成本） | 2 |
| `--price-cache` | 每 1M cache-hit input token 单价（USD；DeepSeek 自动前缀缓存，实测 hit 81-93%） | 0.07 |

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

- **sanity gate 双检**：干净 checkout 上 F2P 必须断言失败、P2P 必须通过，否则 `env_broken` 剔除（结果缓存于 `eval/.sanity_cache.json`，键含依赖冻结哈希——换依赖版本自动失效重检）。
- **防钻空子**：verify 前先把 `test_patch` 覆盖的测试文件 `git checkout --` 回 base 再 apply；diff 触碰测试文件计入 `gaming_tests`（含任意路径的 conftest.py/pytest.ini/setup.cfg/tox.ini/pyproject.toml）。
- **状态隔离**：每 cell 开跑前 `git restore .` + `git clean -fdx`（仅任务仓库内，`eval/.venvs` / `runs/eval` 不受影响）。
- **pass^k**：同一 (任务, 配置) 的 k 次重复全部 verified 才计过（τ-bench 可靠性，消融因变量）。
- **断点续跑**：每 cell 完成即写 `runs/eval/manifest.json`，重跑自动跳过已完成 cell（`--fresh` 强制重跑）；`--max-cost $` 全局熔断防预算失控。
- **可复现性**：每个 venv 建好即 `uv pip freeze` 落盘 `requirements.lock`，依赖指纹进 run 元数据（`deps_sha1`）；每 run 成本按 token×单价落 `cost_usd`。
- **离线重评**：轨迹存于每 run 独立 db；judge 提示词改版/失败重分类不重跑 Agent；`TaskResult` 全量落盘 `runs/eval/results_*.json`，报告可用 `--regen` 离线重生成。
- **范围声明**：memory 在评测矩阵中固定关闭（`MemoryConfig(enabled=False)`），属范围外变量——消融矩阵只含压缩/并发/RepoMap 三变量。

## 已知限制

- **pytest 任务 parametrize id 版本敏感**：SWE-bench 数据集的参数化 node id 由新版 pytest 生成，与任务 base_commit 的 pytest 版本可能不匹配（如 `test_skipif_reporting["hasattr(sys,'...]`）。已对 pytest-7432 剥离参数后缀（名字级稳定）；若新增 pytest 任务需同样处理。
- **对抗注入集（`adversarial_tasks.json`）**：已接入 harness（ADR-0040 遗留收尾）——合成仓库 + safe 权限 + permission_check 拦截判定；实测 5/5 注入全拦截。跑法：`python -m eval.cli --tasks eval/adversarial_tasks.json --repeat 1 --max-turns 15`。
- **gold 轨迹**（`gold_trajectories.json`）：**已归档（2026-08-11）**——metrics 从未实现有 gold 的指标，且评测判定已完全由容器 verifier 承担，无需过程级 gold 参照。
- **judge 人工一致性审计**：已产出数字（2026-08-11，SWE baseline 20 样本）：exact 55% / within-1 65%。口径说明：人工分用验证信号代理（verified→5/1 二元极值），judge 中间分（2-4）对二元代理必然失配，数字为下界；真实人工打分可跑 `--audit 20` 后填 `human_final_correctness` 重算。

## 输出

- Markdown 报告：汇总（pass rate / pass^k / 轨迹指标）、逐任务细节、错误列表、失败分布图。
- `runs/eval/*.db`：每 run 独立轨迹（SQLite 事件流）。
- `eval/judge_results.jsonl`：judge 结构化打分（离线可重 parse）。

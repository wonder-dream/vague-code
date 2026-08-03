# XClaw 评估体系补强 — 全量总结（2026-08-03）

> 会话跨度：2026-08-01 ~ 2026-08-03 ｜ 核心叙事：**EDD（Evaluation-Driven Development）——先把"改动是好是坏"的判断建立在真数据上**
> 对应计划：`docs/plans/0016-eval-methods.md` ｜ 对应代码：`eval/`（11 个模块 + 数据文件 + 12 个测试文件）

---

## 一、出发点（为什么做这套东西）

原 `eval/` 只有 benchmark 壳，且 **pass/fail 是假的**：`harness.py:155` 全标 `True`，`FAIL_TO_PASS`/`PASS_TO_PASS` 验收测试从未执行。意味着：
- 过去所有"改动变好还是变坏"的判断建立在假数据上（开发闭环是断的）
- 简历上 83%/93% 的 pass rate 数字经不起一句"你的验收测试怎么跑的"追问

修复路径 = **P0 真验收 → P0.5 确定性轨迹指标 → P1 LLM-as-Judge → P2 失败分类**，确定性指标进流水线自动跑，LLM judge 独立离线（改提示词只重评分，不重跑 Agent）。

---

## 二、已交付的评估体系（P0-P2 全落地）

### 模块清单（eval/）

| 模块 | 阶段 | 职责 |
|------|------|------|
| `harness.py` | P0 | 驱动 Agent；每 run 独立 db（`runs/eval/<instance>__<cell>.db`）；接 verify + metrics；`--max-turns` 成本控制 |
| `env.py` | P0 | 每 repo uv venv 缓存（`eval/.venvs/<repo>__<commit>/`，`.xclaw_ready` 标记防半成品缓存） |
| `verify.py` | P0 | 验收执行器：状态隔离 / sanity gate 双检 / 防钻空子 / F2P-P2P 判定 / P2P 批量跑 |
| `audit_tasks.py` | P0-5 | 任务质量筛查（SWE-bench Verified 标注法），产出 `audit_results.md`（判定标准定义写进报告开头） |
| `audit_ui.py` | P0-5 | HTML 打分页面 `eval/audit_report.html`（中文速览 + 官方标注预填 + localStorage 打分 + 导出 JSON） |
| `select_verified_tasks.py` | P0 | 从 SWE-bench Lite 选官方保留题重建任务集（配额 + P2P 卫生检查 + pytest 剥参） |
| `reporter.py` | P0-6 | pass^k 可靠性列 + 轨迹指标列 + 失败模式分布段 |
| `metrics.py` | P0.5 | 确定性轨迹指标：read-before-edit / 冗余 read-grep / 编辑→验证循环 / 权限 deny / diff 触碰测试文件 / 轨迹匹配分级（exact/ordered/any/miss + LCS P-R） |
| `judge.py` | P1 | LLM-as-Judge（离线）：manifest/scan 加载、锚定 rubric prompt、鲁棒 JSON 解析+重试、人工一致性审计抽样（硬混一半 verified=False） |
| `rubric.py` | P1 | 锚定 rubric（每维度 1/3/5 分示例），fix_bug/explain 两套 |
| `classify.py` | P2 | 八类失败分类（含伪完成/钻空子型伪完成）+ 失败模式分布图 |

### 数据文件（eval/）

- `tasks.json` — 31 题（30 官方保留 + pytest-7432），全量 node id
- `swe_annotations_ensembled.csv` — OpenAI 官方人工标注全集（1699 样本，数据源）
- `official_annotations.csv` — 当前任务集的官方标注子集
- `gold_trajectories.json` — 待人工标注（骨架）
- `adversarial_tasks.json` — 5 题注入对抗任务（harness 尚未支持执行）
- `.sanity_cache.json` — sanity gate 结果缓存（gitignored）

### 关键机制

- **sanity gate 双检**：干净 checkout 上 F2P 必须断言失败（区分 collection/import error）、P2P 必须通过，否则 env_broken 剔除
- **防钻空子**：verify 前把 test_patch 覆盖的测试文件 `git checkout --` 回 base 再 apply；Agent diff 触碰测试文件计入 `gaming_tests`（在 verify 前抓取，避免被 test_patch 自身污染）
- **状态隔离**：每 cell 前 `git restore .` + `git clean -fdx`（仅任务仓库内）
- **pass^k**：同 (任务, 配置) k 次全 verified 才计过（τ-bench 可靠性，消融因变量）
- **离线重评**：轨迹存每 run 独立 db；judge/分类改版只重评分不重跑 Agent

---

## 三、任务集重建（30 → 31 题）

### 关键发现
- 旧 30 题中 **17 题被 OpenAI SWE-bench Verified 官方人工评审判为应剔除**（描述含糊或测试不公平）——旧 pass rate 跑在脏题集上
- 用官方标准重建：Lite 池 98 题官方保留（排除 django 后 52），按配额选取 + 限制总测试数 ≤100

### 数据质量三重保障
1. **官方筛选**：30 题全 `filter_out=False`
2. **node id 全验证**：562 个短名从 test_patch 推导 → 在每个任务 base_commit 上检出真实仓库逐条 grep 验证（P2P 0 问题；F2P 由构造保证）
3. **数据集缺陷修复**：sphinx-8721 的 P2P 是 test_patch 新增的（数据源 bug）→ 选择器内置 P2P 卫生检查；pytest 任务 parametrize id 版本敏感 → 剥参

### 当前任务集构成（31 题）

| 仓库 | 数量 | 本机可跑 | 说明 |
|------|------|---------|------|
| sympy/sympy | 17 | ✅ 17 | 2017-2022，py3.9/3.11 按日期选版 |
| scikit-learn | 7 | ❌ | MSVC 编译核心，本机无 |
| astropy | 3 | ❌ | 同上 |
| sphinx | 3 | ✅ 2 | 8721 判别器不复现 |
| pytest | 1 | ✅ 1 | src 布局 + 剥参 |

**本机可跑 20 题**；审计报告：保留 20 / 剔除 11（全是机器环境限制，非任务质量问题）。

---

## 四、环境策展（5 仓，18 commit 前哨）

### 策略：不构建仓库本体
本机无 MSVC（Visual C++ Build Tools）→ C 扩展无法源码构建。改为：
- **只装依赖 wheel**，测试时 `PYTHONPATH=<workdir>`（+ `src/` 若存在）让 import 命中任务 base_commit 源码
- **sysmodules 桩**：`shims/sitecustomize.py` 在解释器启动时把编译守卫模块注入 `sys.modules`（`_compiler`/`_column_mixins` 纯 Python 近似，其余"导入安全、调用报错"）
- **按提交日期分版本**：`python_by_date`（sympy 3.9/3.11）+ `install_by_date`（旧 sphinx 的 Jinja2<3.1/docutils<0.18/旧 contrib/alabaster pin）

### 踩坑记录（面试素材，全沉淀进代码）

| 坑 | 现象 | 解法 |
|----|------|------|
| uv venv 无 pip | `python -m pip` 失败 | `uv pip install --python <venv>` |
| `--python` 相对路径 + cwd=workdir | 双路径错位 | venv 路径 `.resolve()` |
| 装一半的 venv 被缓存 | 重复失败 | `.xclaw_ready` 标记 |
| `setuptools.dep_util` 被删 | 旧仓库构建失败 | 不构建即无此问题 |
| numpy 2.x 删 `np.core` | astropy 导入炸 | 全配方 `numpy<2` |
| `python -m pytest` 缺模块无前缀 | classify 误判 fail | 检测 `No module named` |
| 批量 pytest 传参 join 成字符串 | no tests ran | node id 逐个成参 |
| 相对 PYTHONPATH + cwd=workdir | sys.path 双路径 | 全绝对路径 |
| parametrize id 版本敏感 | pytest 自家套件对不上 | 剥参（名字级稳定） |
| sitecustomize 生成语法 | dunder 探测炸 | json.dumps 嵌入 + dunder 返回 AttributeError |
| Windows 文件锁 | rmtree/clone 失败 | 重试 + cmd rmdir 兜底 |

### 实证验证结果
- sympy 17/17、sphinx 2/3、pytest 1/1 sanity gate **PASS**（F2P 断言失败 / P2P 全过）
- sklearn/astropy 实证 env_broken（`__check_build` 编译门 / import 链 Cython 模块）

---

## 五、真实运行验证（真 API）

| 场景 | 结果 |
|------|------|
| 合成 fib 仓库端到端 | Agent 真修好：verify `verified=True`（F2P/P2P 全过）、judge 5/5/5/3 pass、classify success |
| pylint-6506（真实 SWE 任务） | 25 轮撞 max_turns 未产出：verify `no_diff`、classify timeout（修正后） |
| **run_eval 集成冒烟**（sympy-15345 × 1 cell × 15 轮） | **全链路一次跑通**：克隆→env 缓存→sanity 缓存→Agent（51.8K tokens）→verify 正确判 `f2p:fail`→metrics→独立 db；成本几分钱 |

---

## 六、测试与质量门

- **584 passed**（+37 新增 eval 测试）；2 个失败是用户 WIP 的 TUI flaky（时序竞态，与本工作无关）
- ruff 全清、mypy src 零错误
- fake CLI 端到端可跑（无需 API/环境）

---

## 七、待办（按优先级）

| 优先级 | 事项 | 类型 | 说明 |
|--------|------|------|------|
| P0 | **20 题基线消融** | 决策+成本 | `python -m eval.cli --tasks eval/tasks.json --max-turns 25 --repeat 3`（约 ¥20-50）；之后决定是否全矩阵（8 配置 × 3 重复 ≈ 480 次 ≈ ¥200-400） |
| P1 | gold 轨迹标注 5-10 题 | 人工 | 我可先从真实解出轨迹生成草稿 |
| P1 | judge 人工一致性审计（20 样本） | 人工 | `python -m eval.judge --audit 20` → 打分 → `--consistency` 出数字 |
| P2 | 对抗注入 harness 支持 | 代码 | `task_type=adversarial` 执行与拦截判定 |
| P2 | sklearn/astropy 上 Linux/CI | 环境 | 有编译工具链即可；REPO_SETUP 切 editable 构建 |
| P3 | TUI 两个 flaky 测试 | 用户 WIP | 打字/点击竞态，建议改直接调 handler（同文件有先例） |

---

## 八、面试故事线（简历数字口径）

1. **EDD 起点**："我发现我的 83% pass rate 是假的——验收测试从没跑过，于是先修评测闭环"
2. **官方标注筛查**："按 SWE-bench Verified 方法引入 OpenAI 官方人工标注，发现 30 题里 17 题被专业工程师判为脏题，重建任务集"
3. **策展工程**："本机无 MSVC，用 PYTHONPATH 源码策略 + sysmodules 桩绕开编译，按提交日期分依赖版本，5 个仓库全部 sanity gate 实证验证"
4. **过程级评估**："事件流轨迹给了别人没有的过程数据：read-before-edit、验证闭环、八类失败分类"
5. **诚实边界**："本机可跑 20/31 题，其余需 Linux/CI——数字只报能实证的"

---

## 九、命令速查

```bash
# 框架验证（无需 API/环境）
python -m eval.cli --tasks eval/tasks_test.json --fake

# 真消融（基线：20 题可跑子集）
python -m eval.cli --tasks eval/tasks.json --max-turns 25 --repeat 3 --out eval_report.md

# 任务筛查
python -m eval.audit_tasks --init    # 重新生成打分骨架
python -m eval.audit_ui              # 生成/刷新 HTML 打分页
python -m eval.audit_tasks           # 生成审计报告

# LLM-as-Judge（离线）
python -m eval.judge --runs runs/eval --judge-model deepseek-v4-flash
python -m eval.judge --runs runs/eval --audit 20          # 人工审计样本
python -m eval.judge --consistency eval/judge_audit_samples.json

# 任务集重建（需 HF 网络）
python -m eval.select_verified_tasks
```

---

## 十、本次会话 commit 列表（17 个）

```
491eefc docs: add eval-methods plan
1d2723a feat: real eval acceptance — per-run trajectories, uv venv cache, sanity-gated verify, task audit, pass^k
8e51664 feat: offline LLM-as-Judge, deterministic trajectory metrics, eight-class failure classification
e7c2b74 docs: document evaluation pipeline and update project completion checklist
97a4fad fix: resolve full node ids for sympy eval tasks from test patches
8476e23 feat: embed task context in audit scoring skeleton
64ecc10 feat: HTML scoring page for task audit; null-default skeleton
a50ebef feat: add Chinese summaries to audit scoring page
6218170 feat: import OpenAI SWE-bench Verified human annotations into task audit
cee12ed feat: select 30 official-kept SWE-bench tasks via OpenAI Verified annotations
a54321c feat: batch P2P verification runs; Chinese summaries for new task set
7d19536 feat: curate per-repo eval envs — PYTHONPATH source strategy, MSVC shims, per-commit deps
eeb16e6 fix: absolute PYTHONPATH with src-layout support; add pytest env recipe
4556098 feat: add pytest-7432 as 20th runnable eval task
b201d06 feat: max-turns cost control and failure distribution section in report
3648655 chore: datasets dev dep; strip pytest params in task selection
6c7bd5b docs: update eval README env status and known limits; drop stale results.md
```

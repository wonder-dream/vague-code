# T4：运行消融实验

> ⚠️ **过时声明（2026-08）**：本文档描述 v0.1 消融流程。2026-08 起评测已升级：
> ① 任务集按 **OpenAI 官方标注**重建（31 题，本机可跑 20 题），旧 30 题含 17 道脏题；
> ② 验收改为 **sanity gate 双检 + F2P/P2P 实跑**（不再全标 True），新增 **pass^k** 可靠性指标；
> ③ 环境改为 `eval/env.py` 的 `REPO_SETUP` 策展（PYTHONPATH 源码策略），不再手工装依赖。
> 现行流程以 [`eval/README.md`](../../eval/README.md) 为准；重建与策展见
> [`docs/handoff/2026-08-03-xclaw-eval-system.md`](../handoff/2026-08-03-xclaw-eval-system.md)。
> 本文档 v0.1 的 83%/93% 等数字基于假 pass/fail，**不得引用**。

**谁需要读：** 想运行评测并复现消融实验结果的开发者
**前置阅读：** 12-evaluation-harness.md（理解评测架构）、T3（理解任务格式）
**读完能做什么：** 从零跑消融实验，理解配置矩阵，解读报告数字

---

## 1. 准备工作

### 前置条件

- DeepSeek API Key（有足够余额完成 N 次 Agent.run()，`--max-turns` 控制单次成本）
- 足够的磁盘空间（SWE-bench 仓库 blob:none 部分克隆，约数百 MB）
- Git ≥ 2.30、uv（venv 构建）、本机 20 题无需 MSVC（sklearn/astropy 需 Linux/CI）
- 环境已策展：`eval/env.py` REPO_SETUP 覆盖 sympy/sphinx/pytest；`eval/.sanity_cache.json` 已有双检结果

### 验证框架安装

```bash
python -m eval.cli --tasks eval/tasks_test.json --fake
```

预期输出：

```
Loaded 1 tasks from eval/tasks_test.json
Matrix: 24 cells (EvalCell)
Using FakeBackend (no API calls)
[nc_sx_nm_r0] myrepo__task → cloning repo... → done in 3.2s
[C_X_M_r0] myrepo__task → cloning repo... → done in 3.1s
...
Done. Total: 24 | Passed: 0 | Failed: 0 | Errors: 0
```

`--fake` 使用 `_FakeBackend()`（`harness.py:129-141`）模拟 LLM，始终返回 `TextBlock("ok")` + `end_turn`。这是零 API 成本的框架验证——确认任务配置、矩阵展开、报告生成全部正确。

---

## 2. 矩阵配置

**build_matrix(repeat=3)**（`matrix.py:26-38`）展开为 2×2×2×3 = 24 个 cell（compression × concurrency × repo_map × repeat）：

| 序号 | compression | concurrency | repo_map | repeat |
|------|-------------|-------------|----------|--------|
| 1 | ✗ | ✗ | ✗ | 0 |
| 2 | ✗ | ✗ | ✗ | 1 |
| 3 | ✗ | ✗ | ✗ | 2 |
| 4 | ✗ | ✗ | ✓ | 0 |
| 5 | ✗ | ✗ | ✓ | 1 |
| 6 | ✗ | ✗ | ✓ | 2 |
| 7 | ✗ | ✓ | ✗ | 0 |
| ... | ... | ... | ... | ... |
| 24 | ✓ | ✓ | ✓ | 2 |

**为什么需要 repeat=3？** LLM 输出非确定性——同配置不同重复可能结果不同。repeat=3 取平均降低噪音；现行报告另有 **pass^k**（k 次全过才计，度量可靠性）。规模：31 题（本机可跑 20 题）× 8 配置 × 3 重复，`--max-turns` 控制单次成本。

> **repo_map 变量**（ADR-0016 新增）：验证代码理解索引是否减少探索轮次。接入前的消融数据见 12-evaluation-harness.md 第 7 节，接入后数值待真实 API 重跑。

---

## 3. 运行 FakeBackend 验证

```bash
python -m eval.cli --tasks eval/tasks_test.json --fake --out test_report.md
```

报告文件 `test_report.md` 结构（FakeBackend 测试）：

```markdown
# 消融实验结果

总任务数: 1
总运行次数: 12

## 汇总
| 压缩 | 并发 | 重复 | 通过率 | 平均轮次 | 平均 input tokens |
|------|------|------|--------|----------|-------------------|
| ✗ | ✗ | 0 | 0% | 0.0 | 0 |

## 逐任务细节
| 任务ID | 配置 | 通过 | 轮次 | input tokens |
|--------|------|------|------|--------------|
| myrepo__task | nc_sx_r0 | ? | 0 | 0 |

## 错误
```

**常见验证失败原因：**

| 错误 | 原因 | 解决 |
|------|------|------|
| `checkout failed` | Git clone 失败 | 手动验证 repo URL |
| `cant_read_trajectory` | SQLite 路径错误 | 检查 `--db-path` |
| `No tasks.json` | 文件未找到 | 检查 `--tasks` 路径 |

---

## 4. 运行真实 API 评测

### 完整命令

```bash
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 3 --out ablation_report.md
```

### 成本估算

360 次 Agent.run()，每次 ≈ 600K-900K tokens，总计 ≈ 200M-300M tokens。建议在运行前先检查 DeepSeek 当前定价。

### 从小规模开始

先跑 1 个 task × 1 个配置 × 1 次，验证一切正常：

```bash
python -m eval.cli --tasks eval/tasks_test.json --model deepseek-v4-flash --repeat 1 --out test_real_report.md
```

### 正式运行（后台 + 日志重定向）

```bash
nohup python -m eval.cli \
    --tasks eval/tasks.json \
    --model deepseek-v4-flash \
    --repeat 3 \
    --out ablation_report.md \
    > eval.log 2>&1 &
```

监控进度：`tail -f eval.log`

---

## 5. 报告解读

打开报告（`--out` 指定路径），v0.1 汇总表形态：

| 压缩 | 并发 | 通过率 | 平均轮次 | 平均 token |
|------|------|--------|----------|-----------|
| ✗ | ✗ | 基线待真数据 | ... | ... |

> ⚠️ v0.1 的 83%/93%/76%/73% 基于**假 pass/fail**（验收测试未实跑），已废弃，**不得引用**。
> 现行报告新增：`pass^k`（τ-bench 可靠性，k 次全过才计）、轨迹指标（read-before-edit 等）、
> 失败模式分布（八类）。真数字待 20 题基线消融产出后回填。

### 逐行解读（以真实数据回填后参照）

- **通过率**：以 `verified=True`（F2P+P2P 实跑全过）为准，不再有"运行完成即通过"
- **pass^k**：同一 (任务, 配置) 的 k 次重复全部 verified 才计过——度量可靠性而非单次运气
- **轨迹指标**：read-before-edit 合规率、编辑→验证闭环、冗余工具调用等过程质量
- **失败模式分布**：理解错/改错/测试不过/超时/权限误拦/伪完成/钻空子等八类占比 → 指导该修压缩/权限/提示词

---

## 6. 常见陷阱

| 陷阱 | 表现 | 预防/解决 |
|------|------|----------|
| 磁盘空间 | Git clone 失败 | 确认 `--workdir` 所在分区有足够空间 |
| API Rate Limit | 日志大量 rate_limit 重试 | 增加 `--retry-max-delay-s` |
| Git clone 失败 | `checkout failed` | 手动验证 repo URL / 网络（GitHub 可达性） |
| 环境没策展 | `EnvNotCurated` | 在 `eval/env.py` REPO_SETUP 加 install 配方，跑 sanity gate 验证 |
| sanity gate 不过 | `env_broken` | F2P 必须断言失败、P2P 必须通过；判别器不复现或依赖缺失都算 env 问题 |
| 任务标 env_broken | 任务被剔除统计 | 本机无 MSVC → sklearn/astropy 需 Linux/CI 跑 |
| SQLite 数据库堆积 | run 越多 db 越大 | 定期清理 `runs/eval/` |
| 消耗超预期 | token 成本超过预算 | `--max-turns 25` 起 + `--repeat 1` 先估算 |

### 费用控制建议

1. **首次跑**：`--repeat 1` + `tasks_test.json`（1 个 task）→ 看成本和耗时
2. **小规模**：`--max-turns 25 --repeat 1` + 20 题可跑子集 → 基线数字
3. **全量消融**：`--repeat 3` + 8 配置矩阵 → 全量评估（≈480 次运行，控制成本）

---

## 下一篇

→ **R1：AgentConfig 参考**：所有配置字段的完整说明。

**相关链接：** 12-evaluation-harness.md、eval/README.md、docs/plans/0016-eval-methods.md

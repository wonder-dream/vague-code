# 细纲：04-running-ablation-experiments.md

**预估行数：** ~350 行
**定位：** 运行消融实验的完整教程。

---

## 开头

- **谁需要读：** 想运行评测并复现消融实验结果的开发者
- **前置阅读：** 12-evaluation-harness.md（理解评测架构）、T3（理解任务格式）
- **读完能做什么：** 从零跑消融实验，理解配置矩阵，解读报告数字

---

## 细纲

### 1. 准备工作（~30 行）

**前置条件：**
- DeepSeek API Key（有足够余额完成 360 次 Agent.run()）
- 足够的磁盘空间（SWE-bench 仓库约 1-2GB）
- Git ≥ 2.30

**验证框架安装：**
```bash
python -m eval.cli --tasks eval/tasks_test.json --fake
```

**预期输出：**
```
Loaded 1 tasks from eval/tasks_test.json
Matrix: 24 cells (EvalCell)
Using FakeBackend (no API calls)
[nc_sx_nm_r0] myrepo__task → cloning repo... → done in 3.2s
[C_X_M_r0] myrepo__task → cloning repo... → done in 3.1s
...
Done. Total: 24 | Passed: 0 | Failed: 0 | Errors: 0
```

**`--fake` 的功能：**
- `_FakeBackend()`（`harness.py:129-141`）模拟 LLM，始终返回 `TextBlock("ok")` + `end_turn`
- 零 API 成本验证框架路径和图式
- 自动限制到 1 个 task × 1 个 cell

### 2. 矩阵配置（~60 行）

**`build_matrix()`（`matrix.py:26-38`）：**
```python
def build_matrix(repeat: int = 3) -> list[EvalCell]:
    return [
        EvalCell(compression, concurrency, repo_map, rep)
        for compression in [True, False]
        for concurrency in [True, False]
        for repo_map in [True, False]
        for rep in range(repeat)
    ]
```

**矩阵展开（repeat=3 → 24 个 cell）：**

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

**为什么需要 repeat 3：**
- LLM 输出非确定性→同配置不同重复可能结果不同
- repeat=3 取平均→降低噪音
- 30 题 × 8 配置 × 3 重复 = 720 次 Agent.run()

> **repo_map 变量**（ADR-0016 新增）：验证代码理解索引是否减少探索轮次。接入前的消融数据见 `docs/articles/12-evaluation-harness.md` 第 7 节，接入后数值待真实 API 重跑。

### 3. 运行 FakeBackend 验证（~40 行）

**完整验证命令：**
```bash
python -m eval.cli --tasks eval/tasks_test.json --fake --out test_report.md
```

**报告文件 `test_report.md` 结构（用代码块展示内容）：**
```markdown
# 消融实验结果

总任务数: 1
总运行次数: 12

## 汇总
| 压缩 | 并发 | 重复 | 通过率 | 平均轮次 | 平均 input tokens | stale回收 | micro回收 | auto回收 | truncate回收 |
|------|------|------|--------|----------|-------------------|-----------|-----------|----------|--------------|
| ✗ | ✗ | 0 | 0% | 0.0 | 0 | 0 | 0 | 0 | 0 |

## 逐任务细节
| 任务ID | 配置 | 通过 | 轮次 | input tokens | run_end_reason |
|--------|------|------|------|--------------|----------------|
| myrepo__task | nc_sx_r0 | ? | 0 | 0 | end_turn |

## 错误
```

**常见验证失败原因：**

| 错误 | 原因 | 解决 |
|------|------|------|
| `checkout failed` | Git clone 失败（网络/权限） | 手动验证 repo URL |
| `cant_read_trajectory` | SQLite 数据库路径错误 | 检查 `--db-path` |
| `No tasks.json` | 文件未找到 | 检查 `--tasks` 路径 |

### 4. 运行真实 API 评测（~60 行）

**完整命令：**
```bash
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 3 --out ablation_report.md
```

**⚠️ 成本估算：**
- 360 次 Agent.run()
- 每次 ≈ 600K-900K tokens
- 总 token ≈ 200M-300M
- 成本取决于 API 当前价格（建议先检查 DeepSeek 定价页）

**小规模验证（先跑 1 个 task × 1 个配置 × 1 次）：**
```bash
python -m eval.cli --tasks eval/tasks_test.json --model deepseek-v4-flash --repeat 1 --out test_real_report.md
```

**正式运行（后台 + 日志重定向）：**
```bash
nohup python -m eval.cli \
    --tasks eval/tasks.json \
    --model deepseek-v4-flash \
    --repeat 3 \
    --out ablation_report.md \
    > eval.log 2>&1 &
```

**监控进度：** `tail -f eval.log`

### 5. 报告解读（~100 行）

**打开 `ablation_report.md`，查看汇总表：**

| 压缩 | 并发 | 通过率 | 平均轮次 | 平均 token | stale回收 | micro回收 | auto回收 | truncate回收 |
|------|------|--------|----------|-----------|-----------|-----------|----------|--------------|
| ✗ | ✗ | 83% | 20.8 | 635K | 0 | 0 | 0 | 0 |
| ✗ | ✓ | 93% | 20.3 | 614K | 0 | 0 | 0 | 0 |
| ✓ | ✗ | 76% | 22.2 | 735K | 8K | 0 | 0 | 0 |
| ✓ | ✓ | 73% | 21.6 | 759K | 5K | 0 | 0 | 0 |

**逐行解读：**

**第 1 行（基线，concurrency=off, compression=off）：**
- 83% pass rate（30 题中 ~25 题通过）
- 平均 20.8 轮，635K input tokens
- 对比基线（max_turns=30, 60%, 931K）：max_turns=50 提高成功率 23pp

**第 2 行（最大增益，concurrency=on, compression=off）：**
- **93% pass rate — 全部最佳**
- 614K tokens — 最低消耗
- 结论：并发既提高成功率又节省 token

**第 3 行（compression=on, concurrency=off，`eval/results.md:3-8`）：**
- 76% pass rate（低于 83%）
- 735K tokens（高于 635K）
- stale 回收仅 8K——30 轮以下 Agent 不会反复读同一文件
- 原因：auto_compact 的 LLM 调用消耗 > 回收收益

**第 4 行（compression=on, concurrency=on）：**
- 73% pass rate（低于单独的 83% 和 93%）
- 759K tokens（最高）
- **并发+压缩负协同：** auto_compact 与主 LLM 调用共享 backend 产生竞争

**与基线（`results.md:10-14`）对比：**

| 配置 | 对比基线 (60%, 931K) |
|------|---------------------|
| C=off, X=off | +23pp pass rate, -32% tokens |
| C=off, X=on | +33pp pass rate, -34% tokens |
| C=on, X=off | +16pp pass rate, -21% tokens |
| C=on, X=on | +13pp pass rate, -18% tokens |

**关键洞察：**
1. 并发是最大收益来源（+33pp，-34% tokens）
2. 压缩在短会话中负收益——设计目标为 30+ 轮长会话
3. 超 50 轮的场景下压缩可能正收益（需要额外实验验证）

### 6. 常见陷阱（~40 行）

| 陷阱 | 表现 | 预防/解决 |
|------|------|----------|
| 磁盘空间 | `Git clone` 失败 | 确认 `--workdir` 所在分区有足够空间 |
| API Rate Limit | 日志大量 `rate_limit` 重试 | 增加 `--retry-max-delay-s` / 减少并发调用量 |
| Git clone 失败 | `checkout failed` | 手动验证 repo URL 并添加至可访问列表 |
| verify 脚本依赖不对 | 验收测试失败 | 检查任务的 `FAIL_TO_PASS` 中 pip 安装步骤 |
| SQLite 数据库堆积 | run 越多 db 越大 | 定期清理 `runs/runs.db` 或指定 `--db-path` |
| 消耗超预期 | token 成本超过预算 | 先用 `--repeat 1` + `--tasks tasks_test.json` 估算 |  

**费用控制建议：**
1. 首次跑：`--repeat 1` + `tasks_test.json`（1 个 task）→ 看成本和耗时
2. 小规模：`--repeat 1` + `tasks.json`（30 个 task）→ 看整体成本
3. 全量：`--repeat 3` + `tasks.json` → 全量评估

---

## 结尾

**下一篇推荐：** → R1（API 参考：AgentConfig）
**相关链接：** 12-evaluation-harness.md、eval/README.md、eval/results.md

---

## 本文件说明

这是文档 `04-running-ablation-experiments.md` 的细纲（大纲）。实际写作时所有消融数据应引用 `eval/results.md` 的最新结果。成本估算需在写作时更新 API 价格。

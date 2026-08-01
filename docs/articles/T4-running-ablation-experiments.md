# T4：运行消融实验

**谁需要读：** 想运行评测并复现消融实验结果的开发者
**前置阅读：** 12-evaluation-harness.md（理解评测架构）、T3（理解任务格式）
**读完能做什么：** 从零跑消融实验，理解配置矩阵，解读报告数字

---

## 1. 准备工作

### 前置条件

- DeepSeek API Key（有足够余额完成 360 次 Agent.run()）
- 足够的磁盘空间（SWE-bench 仓库约 1-2GB）
- Git ≥ 2.30

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

**为什么需要 repeat=3？** LLM 输出非确定性——同配置不同重复可能结果不同。repeat=3 取平均降低噪音。30 题 × 8 配置 × 3 重复 = 720 次 Agent.run()。

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

打开 `ablation_report.md`，查看汇总表：

| 压缩 | 并发 | 通过率 | 平均轮次 | 平均 token |
|------|------|--------|----------|-----------|
| ✗ | ✗ | 83% | 20.8 | 635K |
| ✗ | ✓ | 93% | 20.3 | 614K |
| ✓ | ✗ | 76% | 22.2 | 735K |
| ✓ | ✓ | 73% | 21.6 | 759K |

### 逐行解读

**第 1 行（基线，concurrency=off, compression=off）：**
- 83% pass rate（30 题中 ~25 题通过）
- 平均 20.8 轮，635K input tokens
- 对比初版基线（max_turns=30, 60%, 931K）：max_turns=50 提高成功率 23pp，token 降低 32%

**第 2 行（最大增益，concurrency=on, compression=off）：**
- **93% pass rate — 全表最佳**
- **614K tokens — 最低消耗**
- 并发既提高成功率又节省 token：多工具并行不增加语义轮次

**第 3 行（compression=on, concurrency=off）：**
- 76% pass rate（低于 83%）
- 735K tokens（高于 635K）
- stale 回收仅 8K——30 轮以下 Agent 不会反复读同一文件
- auto_compact 的 LLM 调用成本高于回收的 token 空间

**第 4 行（compression=on, concurrency=on）：**
- 73% pass rate（低于单独的 83% 和 93%）
- 759K tokens（全表最高）
- **并发+压缩负协同：** auto_compact 的摘要请求与主 Agent LLM 调用共享 backend，产生资源竞争

### 与基线的对比

| 配置 | 对比基线 (60%, 931K) |
|------|---------------------|
| C=off, X=off | +23pp pass rate, -32% tokens |
| C=off, X=on | **+33pp pass rate, -34% tokens** |
| C=on, X=off | +16pp pass rate, -21% tokens |
| C=on, X=on | +13pp pass rate, -18% tokens |

### 关键洞察

1. **并发是最大收益来源**（+33pp, -34% tokens）——2×2 消融实验中最明确的结论
2. **压缩在短会话中负收益**——设计目标为 30+ 轮长会话
3. **压缩+并发存在负协同**——需要进一步分析 auto_compact 的调度策略
4. 超 50 轮的场景下压缩可能正收益（需要额外实验验证）

---

## 6. 常见陷阱

| 陷阱 | 表现 | 预防/解决 |
|------|------|----------|
| 磁盘空间 | Git clone 失败 | 确认 `--workdir` 所在分区有足够空间 |
| API Rate Limit | 日志大量 rate_limit 重试 | 增加 `--retry-max-delay-s` |
| Git clone 失败 | `checkout failed` | 手动验证 repo URL |
| verify 脚本依赖不对 | 验收测试失败 | 检查任务中的 pip 安装步骤 |
| SQLite 数据库堆积 | run 越多 db 越大 | 定期清理 `runs/runs.db` |
| 消耗超预期 | token 成本超过预算 | 先用 `--repeat 1` 估算 |

### 费用控制建议

1. **首次跑**：`--repeat 1` + `tasks_test.json`（1 个 task）→ 看成本和耗时
2. **小规模**：`--repeat 1` + `tasks.json`（30 个 task）→ 看整体成本
3. **全量**：`--repeat 3` + `tasks.json` → 全量评估

---

## 下一篇

→ **R1：AgentConfig 参考**：所有配置字段的完整说明。

**相关链接：** 12-evaluation-harness.md、eval/README.md、eval/results.md

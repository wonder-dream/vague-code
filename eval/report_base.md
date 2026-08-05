# 消融实验结果

总任务数: 8
总运行次数: 8
总成本: $0.9230（按评测时 cli 单价估）

## 汇总

| 压缩 | 并发 | RepoMap | 重复 | 通过率 | 平均轮次 | 平均 input tokens | code_search | stale回收 | micro回收 | ssnip回收 | auto回收 | truncate回收 | 成本($) |
|------|------|---------|------|--------|----------|-------------------|-------------|-----------|-----------|-----------|----------|--------------|---------|
| ✓ | ✓ | ✓ | 0 | 25% | 21.9 | 847,659 | 0 | 103,376 | 0 | 0 | 0 | 0 | $0.92 |

## 逐任务细节

| 任务ID | 配置 | 通过 | verified | 判定 | 轮次 | input tokens | run_end_reason |
|--------|------|------|----------|------|------|--------------|----------------|
| pytest-dev__pytest-7432 | compression=1_concurrency=1_repo_map=1 | ✗ | ✗ | no_diff | 25 | 1,454,977 | max_turns |
| sphinx-doc__sphinx-8595 | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | ok | 25 | 976,806 | max_turns |
| sympy__sympy-12419 | compression=1_concurrency=1_repo_map=1 | ? | - | - | - | 0 | - |
| sympy__sympy-12481 | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | ok | 25 | 1,023,871 | max_turns |
| sympy__sympy-15345 | compression=1_concurrency=1_repo_map=1 | ✗ | ✗ | f2p:fail | 25 | 367,533 | max_turns |
| sympy__sympy-20590 | compression=1_concurrency=1_repo_map=1 | ✗ | ✗ | no_diff | 25 | 1,172,069 | max_turns |
| sympy__sympy-21612 | compression=1_concurrency=1_repo_map=1 | ✗ | ✗ | no_diff | 25 | 716,095 | max_turns |
| sympy__sympy-23262 | compression=1_concurrency=1_repo_map=1 | ✗ | ✗ | f2p:no_tests | 25 | 1,069,928 | max_turns |

## pass^k 可靠性（τ-bench：k 次全过才计过）

| 配置 | k | 全过任务数 | 任务总数 | pass^k |
|------|---|------------|----------|--------|
| compression=1_concurrency=1_repo_map=1 | 1 | 2 | 7 | 29% |

整体 pass^k: 2/7 = 29%

## 逐题胜负表（单变量 on/off，pass^k 粒度）


### compression 开 vs 关（concurrency=True, repo_map=True）

- 开过/关不过: 2 题 → sphinx-doc__sphinx-8595, sympy__sympy-12481
- 关过/开不过: 0 题 → -

### concurrency 开 vs 关（compression=True, repo_map=True）

- 开过/关不过: 2 题 → sphinx-doc__sphinx-8595, sympy__sympy-12481
- 关过/开不过: 0 题 → -

### repo_map 开 vs 关（compression=True, concurrency=True）

- 开过/关不过: 2 题 → sphinx-doc__sphinx-8595, sympy__sympy-12481
- 关过/开不过: 0 题 → -

## 轨迹指标（P0.5 确定性，平均 per run）

| 配置 | 工具数 | 冗余read | 冗余grep | 错误调用 | read→edit | edit→test | 权限deny | 触碰测试文件 |
|------|--------|----------|----------|----------|-----------|-----------|----------|---------------|
| compression=1_concurrency=1_repo_map=1 | 36.0 | 1.57 | 2.71 | 9.71 | 0.14 | 0.14 | 0.0 | 1 |

## 失败模式分布（P2 分类）

| 类别 | 数量 | 占比 |
|------|------|------|
| 超时 | 5 | 62% |
| 成功 | 2 | 25% |
| 测试不过(含env) | 1 | 12% |

## 错误

- `sympy__sympy-12419` (compression=1_concurrency=1_repo_map=1): checkout failed: [WinError 2] 系统找不到指定的文件。: 'eval\\.workdir\\sympy__sympy-12419'
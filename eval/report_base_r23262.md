# 消融实验结果

总任务数: 1
总运行次数: 1
总成本: $0.2008（按评测时 cli 单价估）

## 汇总

| 压缩 | 并发 | RepoMap | 重复 | 通过率 | 平均轮次 | 平均 input tokens | code_search | stale回收 | micro回收 | ssnip回收 | auto回收 | truncate回收 | 成本($) |
|------|------|---------|------|--------|----------|-------------------|-------------|-----------|-----------|-----------|----------|--------------|---------|
| ✓ | ✓ | ✓ | 0 | 100% | 25.0 | 1,398,659 | 0 | 25,159 | 0 | 0 | 0 | 0 | $0.20 |

## 逐任务细节

| 任务ID | 配置 | 通过 | verified | 判定 | 轮次 | input tokens | run_end_reason |
|--------|------|------|----------|------|------|--------------|----------------|
| sympy__sympy-23262 | compression=1_concurrency=1_repo_map=1 | ✓ | ✓ | ok | 25 | 1,398,659 | max_turns |

## pass^k 可靠性（τ-bench：k 次全过才计过）

| 配置 | k | 全过任务数 | 任务总数 | pass^k |
|------|---|------------|----------|--------|
| compression=1_concurrency=1_repo_map=1 | 1 | 1 | 1 | 100% |

整体 pass^k: 1/1 = 100%

## 逐题胜负表（单变量 on/off，pass^k 粒度）


### compression 开 vs 关（concurrency=True, repo_map=True）

- 开过/关不过: 1 题 → sympy__sympy-23262
- 关过/开不过: 0 题 → -

### concurrency 开 vs 关（compression=True, repo_map=True）

- 开过/关不过: 1 题 → sympy__sympy-23262
- 关过/开不过: 0 题 → -

### repo_map 开 vs 关（compression=True, concurrency=True）

- 开过/关不过: 1 题 → sympy__sympy-23262
- 关过/开不过: 0 题 → -

## 轨迹指标（P0.5 确定性，平均 per run）

| 配置 | 工具数 | 冗余read | 冗余grep | 错误调用 | read→edit | edit→test | 权限deny | 触碰测试文件 |
|------|--------|----------|----------|----------|-----------|-----------|----------|---------------|
| compression=1_concurrency=1_repo_map=1 | 34.0 | 2.0 | 4.0 | 6.0 | 1.0 | 0.0 | 0.0 | 0 |

## 失败模式分布（P2 分类）

| 类别 | 数量 | 占比 |
|------|------|------|
| 成功 | 1 | 100% |
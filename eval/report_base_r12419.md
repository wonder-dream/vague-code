# 消融实验结果

总任务数: 1
总运行次数: 1

## 汇总

| 压缩 | 并发 | RepoMap | 重复 | 通过率 | 平均轮次 | 平均 input tokens | code_search | stale回收 | micro回收 | ssnip回收 | auto回收 | truncate回收 | 成本($) |
|------|------|---------|------|--------|----------|-------------------|-------------|-----------|-----------|-----------|----------|--------------|---------|
| ✓ | ✓ | ✓ | 0 | 0% | 0.0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | $0.00 |

## 逐任务细节

| 任务ID | 配置 | 通过 | verified | 判定 | 轮次 | input tokens | run_end_reason |
|--------|------|------|----------|------|------|--------------|----------------|
| sympy__sympy-12419 | compression=1_concurrency=1_repo_map=1 | ? | - | - | - | 0 | - |

## 错误

- `sympy__sympy-12419` (compression=1_concurrency=1_repo_map=1): checkout failed: [WinError 2] 系统找不到指定的文件。: 'eval\\.workdir\\sympy__sympy-12419'
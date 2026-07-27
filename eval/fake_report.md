# 消融实验结果

总任务数: 1
总运行次数: 1

## 汇总

| 压缩 | 并发 | 重复 | 通过率 | 平均轮次 | 平均 input tokens | stale回收 | micro回收 | auto回收 | truncate回收 |
|------|------|------|--------|----------|-------------------|-----------|-----------|----------|--------------|
| ✓ | ✓ | 0 | 0% | 0.0 | 0 | 0 | 0 | 0 | 0 |

## 逐任务细节

| 任务ID | 配置 | 通过 | 轮次 | input tokens | run_end_reason |
|--------|------|------|------|--------------|----------------|
| test__test_fix | compression=1_concurrency=1 | ? | - | 0 | - |

## 错误

- `test__test_fix` (compression=1_concurrency=1): checkout failed: Command '['git', 'clone', 'https://github.com/dummy/repo.git', '\\tmp\\xcode_eval\\test__test_fix']' returned non-zero exit status 128.
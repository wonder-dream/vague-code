# 评测框架 (eval/)

消融实验控制层：读取实验矩阵配置，编程式驱动 Agent，收集轨迹，运行验收脚本判定 pass/fail，产出对比报告。

## 用法

```bash
# 验证框架（使用 FakeBackend，无需 API Key）
python -m eval.cli --tasks eval/tasks_test.json --fake

# 运行完整评测（需要 API Key）
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash

# 消融实验矩阵（2×2 compression/concurrency × 3 重复）
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 3 --out report.md
```

## 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--tasks` | 任务定义 JSON 文件 | 必填 |
| `--out` | 输出报告路径 | `eval_report.md` |
| `--repeat` | 每配置重复次数 | 3 |
| `--fake` | 使用 FakeBackend（仅验证框架） | 否 |
| `--workdir` | 任务 repo 工作目录基础路径 | `.` |
| `--model` | 模型名 | `deepseek-v4-flash` |

## 输入格式

`tasks.json` — 每个任务包含：

```json
{
  "instance_id": "repo__issue-id",
  "repo": "owner/repo",
  "base_commit": "abc123...",
  "problem_statement": "Bug description...",
  "FAIL_TO_PASS": ["test cmd 1", "test cmd 2"],
  "PASS_TO_PASS": ["test cmd 3"],
  "test_patch": "diff --git a/test b/test"
}
```

## 输出

Markdown 报告包含：
- 汇总表（pass rate、avg tokens、压缩节省 token）
- 每任务详情表
- 错误列表

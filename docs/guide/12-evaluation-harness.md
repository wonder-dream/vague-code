# 细纲：12-evaluation-harness.md

**预估行数：** ~450 行
**定位：** 自动化评测框架的完整设计。

---

## 开头

- **谁需要读：** 想运行评测或理解评测系统设计的开发者
- **前置阅读：** 04-agent-runtime.md（理解 Agent 编程接口）、10-trajectory.md（理解轨迹数据）
- **读完能做什么：** 运行消融实验、添加评测任务、解读评测报告

---

## 细纲

### 1. 概述（~30 行）

- 架构核心：Agent 即库 → 以编程方式驱动（ADR-0001 的直接收益）
- 评测不是离线日志分析器：
  - **控制自变量：** 构造 AgentConfig（compression on/off、concurrency on/off）
  - **测量因变量：** 从 Trajectory 提取 pass rate、token 消耗、压缩回收率
  - **产报告：** Markdown 对比表
- 评测循环：读任务 → 展开矩阵 → Agent.run() → 验收 → pass/fail

### 2. SWE-bench 任务格式（~40 行）

**`tasks.json` 格式（`eval/tasks.json` 30 题）：**

```json
{
  "instance_id": "django__django-10097",
  "repo": "django/django",
  "base_commit": "abc123def456",
  "problem_statement": "Fix the bug in ...",
  "FAIL_TO_PASS": ["pytest test_a.py", "pytest test_b.py"],
  "PASS_TO_PASS": ["pytest test_c.py", "pytest test_d.py"],
  "test_patch": "diff --git a/test b/test\n..."
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| instance_id | string | 唯一标识（repo__issue-id） |
| repo | string | GitHub 仓库路径 |
| base_commit | string | 引入 bug 之前的 commit |
| problem_statement | string | 任务描述（给 Agent 的 prompt） |
| FAIL_TO_PASS | list[string] | 修 bug 前必挂、修后必过的测试命令 |
| PASS_TO_PASS | list[string] | 修 bug 前后都必过的回归测试命令 |
| test_patch | string | 测试文件的 diff（验证环境用） |

**Fail-to-Pass / Pass-to-Pass 概念：**
- Fail-to-Pass：验证 bug 被真正修复
- Pass-to-Pass：验证没引入新的 bug

**`load_tasks()`（`harness.py:13-15`）：** 从 JSON 文件加载任务列表

### 3. 评测循环（~60 行）

**`run_eval()`（`harness.py:91-163`）：**

```
for cell in matrix:                          # 遍历实验配置
    for task in tasks:                       # 遍历所有任务
        1. _set_workdir(task, base_dir)
           → git clone repo
           → git checkout base_commit

        2. 构造 AgentConfig
           config.compression = cell.compression
           config.concurrent_tools = cell.concurrency
           config.permission_mode = "auto"   # 评测用 auto（零交互）
           config.memory.enabled = False     # 评测关记忆（控制变量）

        3. 创建 Backend（Fake / Real）
           if use_fake:
               backend = _FakeBackend()     # 模拟 LLM
           else:
               backend = DeepSeekBackend()  # 真实 API

        4. agent.run(task["problem_statement"], workdir)

        5. _extract_stats(traj.config.db_path)
           → 从 SQLite 提取因变量

        6. append TaskResult(instance_id, cell, passed?, stats, error?)
```

**`_set_workdir()`（`harness.py:71-88`）：**
```python
workdir = Path(base_dir) / task["instance_id"]
if workdir.exists():
    shutil.rmtree(workdir)
subprocess.run(["git", "clone", repo_url, workdir], check=True, timeout=120)
subprocess.run(["git", "checkout", commit], cwd=workdir, check=True, timeout=30)
```

**`_extract_stats()`（`harness.py:18-68`）——从 SQLite 事件流提取因变量：**

| 因变量 | 类型 | 来源 |
|--------|------|------|
| total_turns | int | 数 `turn_start` 事件 |
| tool_calls | int | 数 `tool_call` 事件 |
| code_search_calls | int | 数 `tool_call` 事件中 `name='code_search'` |
| compression_events | int | 数 `compression` 事件 |
| stale_snip_reclaimed | int | `sum(before_tokens - after_tokens) where layer='stale_snip'` |
| microcompact_reclaimed | int | 同上 layer='microcompact' |
| structured_snip_reclaimed | int | 同上 layer='structured_snip' |
| auto_compact_reclaimed | int | 同上 layer='auto_compact' |
| truncate_reclaimed | int | 同上 layer='truncate' |
| total_input_tokens | int | `sum(llm_response.usage.input_tokens)` |
| total_output_tokens | int | `sum(llm_response.usage.output_tokens)` |
| permission_checks | int | 数 `permission_check` 事件 |
| run_end_reason | string | 最后一条 `run_end` 的 payload.reason |

### 4. 实验矩阵（~40 行）

**`EvalCell`（`matrix.py:8-14`）：**

```python
@dataclass
class EvalCell:
    compression: bool    # 压缩开/关
    concurrency: bool    # 并发开/关
    repo_map: bool       # repo map 开/关
    repeat: int          # 重复次数的标签
```

**`build_matrix(repeat)`（`matrix.py:26-38`）：**
```python
def build_matrix(repeat: int = 3) -> list[EvalCell]:
    cells = []
    for compression in [True, False]:
        for concurrency in [True, False]:
            for repo_map in [True, False]:
                for rep in range(repeat):
                    cells.append(EvalCell(compression, concurrency, repo_map, rep))
    return cells  # 2×2×2×repeat = 24 cells
```

**矩阵展开示例：**
```
Cell 0: compression=✗ concurrency=✗ r0
Cell 1: compression=✗ concurrency=✗ r1
Cell 2: compression=✗ concurrency=✗ r2
Cell 3: compression=✗ concurrency=✓ r0
Cell 4: compression=✗ concurrency=✓ r1
...
Cell 11: compression=✓ concurrency=✓ r2
```

**`cell_label()`（`matrix.py:38-43`）：** `"nc_sx_r0"` | `"C_X_r1"` 等

**`TaskResult`（`matrix.py:15-22`）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| instance_id | str | 任务 ID |
| cell | EvalCell | 实验配置 |
| passed | bool\|None | True=通过，False=未通过，None=错误 |
| error | str\|None | 错误消息 |
| stats | dict | 各因变量的统计值 |
| trajectory_path | str | SQLite 数据库路径 |

### 5. FakeBackend（~30 行）

**代码位置：** `harness.py:129-141`

```python
class _FakeBackend:
    def complete(self, messages, tools=None, config=None) -> ModelResponse:
        return ModelResponse(
            message=Message(role="assistant", content=[TextBlock(text="ok")]),
            stop_reason=StopReason.end_turn,
            usage=NormalizedUsage(input_tokens=10, output_tokens=5),
        )
```

**用途：**
- 零 API 成本验证框架
- 验证：任务配置正确 / 矩阵展开正确 / 报告生成正确
- `--fake` flag 自动限制：只跑 1 个 task × 1 个 cell（`cli.py:31-33`）

### 6. 报告生成（~40 行）

**`generate_report()`（`reporter.py:14-77`）：**

**汇总表格式（`reporter.py:27-54`）：**

| 压缩 | 并发 | 重复 | 通过率 | 平均轮次 | 平均 input tokens | stale回收 | micro回收 | auto回收 | truncate回收 |
|------|------|------|--------|----------|-------------------|-----------|-----------|----------|--------------|
| ✓ | ✗ | 0 | 83% | 22.2 | 735,471 | 8K | 0 | 0 | 0 |
| ✓ | ✓ | 0 | 73% | 21.6 | 759,153 | 5K | 0 | 0 | 0 |

**逐任务细节表（`reporter.py:56-68`）：**

| 任务ID | 配置 | 通过 | 轮次 | input tokens | run_end_reason |
|--------|------|------|------|--------------|----------------|
| django__django-10097 | nc_sx_r0 | ✓ | 18 | 523,400 | end_turn |

**错误列表（`reporter.py:70-75`）：** `- {instance_id} ({cell: ...}): {error}`

### 7. 当前结果（~50 行）

**来源：** `README.md:119-132`、`eval/results.md:3-14`

**基线（max_turns=30，compression=off，concurrency=off）：**
- Pass rate: 60%（18/30 end_turn）
- Avg tokens: 931K / task
- Avg turns: 23.5

**消融实验完整表（max_turns=50，30 题 × 4 配置 × 3 重复）：**

| Compression | Concurrency | Pass Rate | Avg Tokens | 对比基线 |
|------------|-------------|-----------|------------|----------|
| ✗ | ✗ | 83% | 635K | +23pp pass rate, -32% tokens |
| ✗ | ✓ | **93%** | **614K** | **+33pp pass rate, -34% tokens** |
| ✓ | ✗ | 76% | 735K | +16pp pass rate, -21% tokens |
| ✓ | ✓ | 73% | 759K | +13pp pass rate, -18% tokens |

**关键解读：**
1. **并发是最大单项收益**（93% pass rate，token 最少）
2. **压缩在短会话中负收益**（76% < 83%，735K > 635K）——30 轮以下利用率不足
3. **压缩+并发存在负协同**（73% < 76% 和 93%）——auto_compact 与主 LLM 调用共享 backend
4. **压缩目标场景**：30+ 轮长会话

### 8. 添加新任务——攻略（~30 行）

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 创建 task 目录 + `verify.sh` | 验收脚本：接收工作目录、运行 fail-to-pass 和 pass-to-pass |
| 2 | 添加到 `tasks.json` | 遵循 SWE-bench 格式 |
| 3 | FakeBackend 验证 | `python -m eval.cli --tasks eval/tasks_test.json --fake` |
| 4 | 真实 API 单题验证 | `python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 1` |

---

## 结尾

**下一篇推荐：** → T1（教程：你的第一个任务）
**相关链接：** eval/README.md（用法快速参考）、eval/results.md（最新结果）

---

## 本文件说明

这是文档 `12-evaluation-harness.md` 的细纲（大纲）。实际写作时需确认 `eval/tasks.json` 中的具体任务数量和格式。消融数据需引用 `eval/results.md` 的最新结果。

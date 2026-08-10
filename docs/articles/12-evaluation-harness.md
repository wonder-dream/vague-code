# Evaluation Harness

> ⚠️ **过时声明（2026-08）**：本文档描述 v0.1 评测框架。2026-08 已按
> [`docs/plans/0016-eval-methods.md`](../plans/0016-eval-methods.md) 全面升级：
> **真验收**（sanity gate 双检 + F2P/P2P 实跑，不再全标 True）、**pass^k 可靠性指标**、
> 任务集按 **OpenAI SWE-bench Verified 官方标注**重建（17 道脏题剔除，现 31 题/本机可跑 20 题）。
> 现行用法以 [`eval/README.md`](../../eval/README.md) 为准；重建与策展全过程见
> [`docs/handoff/2026-08-03-vague-code-eval-system.md`](../handoff/2026-08-03-vague-code-eval-system.md)。
> 下文保留 v0.1 结构讲解（矩阵/轨迹/报告架构仍成立），"当前结果"一节已被新数据取代。

**谁需要读：** 想运行评测或理解评测系统设计的开发者
**前置阅读：** 04-agent-runtime.md（理解 Agent 编程接口）、10-trajectory.md（理解轨迹数据）
**读完能做什么：** 运行消融实验、添加评测任务、解读评测报告

---

## 1. 概述

评测系统的架构核心来自 ADR-0001 的设计决策：**Agent 即库**。因为 Agent 暴露了 `Agent(config).run(task, workdir) → Trajectory` 的编程接口，评测框架可以**程序化地**驱动 Agent，而不是通过 CLI 解析文本输出。

评测不是离线日志分析器——它是活的实验控制层：
- **控制自变量：** 构造不同的 `AgentConfig`（compression on/off、concurrency on/off）
- **测量因变量：** 从 `Trajectory` 提取 pass rate、token 消耗、压缩回收率
- **产出报告：** Markdown 对比表

评测循环：`读任务 → 展开实验矩阵 → Agent.run() → 验收（sanity gate 双检 + F2P/P2P 实跑）判定 pass/fail`

---

## 2. SWE-bench 任务格式

**tasks.json 格式**（`eval/tasks.json`，31 题，20 题本机可跑）：

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
| problem_statement | string | Agent 收到的任务描述 |
| FAIL_TO_PASS | list[string] | 修 bug 前必挂、修后必过的测试命令 |
| PASS_TO_PASS | list[string] | 修 bug 前后都必过的回归测试命令 |
| test_patch | string | 测试文件的 diff（验证环境用） |

**Fail-to-Pass / Pass-to-Pass** 是 SWE-bench 的核心验收概念：
- **Fail-to-Pass**：验证 bug 被真正修复——原来挂的测试必须过
- **Pass-to-Pass**：验证没引入新的 bug——原来过的测试不能挂

**load_tasks()**（`harness.py:13-15`）：从 JSON 文件加载任务列表。

---

## 3. 评测循环

**run_eval()**（`harness.py:91-163`）：

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

**_set_workdir()**（`harness.py:71-88`）每次跑新任务都是干净的——从目标 commit 重新 git clone，确保测试环境的一致性。

**_extract_stats()**（`harness.py:18-68`）从 SQLite 事件流提取因变量：

| 因变量 | 类型 | 来源 |
|--------|------|------|
| total_turns | int | 数 `turn_start` 事件 |
| tool_calls | int | 数 `tool_call` 事件 |
| code_search_calls | int | 数 `tool_call` 事件中 `name='code_search'` 的 |
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

---

## 4. 实验矩阵

**EvalCell**（`matrix.py:8-14`）：

```python
@dataclass
class EvalCell:
    compression: bool    # 压缩开/关
    concurrency: bool    # 并发开/关
    repo_map: bool       # repo map 开/关
    repeat: int          # 重复次数标签
```

**build_matrix(repeat=3)**（`matrix.py:26-38`）：

```python
def build_matrix(repeat: int = 3) -> list[EvalCell]:
    cells = []
    for compression in [True, False]:
        for concurrency in [True, False]:
            for repo_map in [True, False]:
                for rep in range(repeat):
                    cells.append(EvalCell(compression, concurrency, repo_map, rep))
    return cells  # 2×2×2×repeat
```

矩阵展开为例（2×2×2×3=24 cells）：

```
Cell 0:  compression=✗  concurrency=✗  repo_map=✗  r0
Cell 1:  compression=✗  concurrency=✗  repo_map=✗  r1
Cell 2:  compression=✗  concurrency=✗  repo_map=✗  r2
Cell 3:  compression=✗  concurrency=✗  repo_map=✓  r0
...
Cell 11: compression=✗  concurrency=✓  repo_map=✓  r2
...
Cell 23: compression=✓  concurrency=✓  repo_map=✓  r2
```

**cell_label**：`C/nc`（压缩）`X/sx`（并发）`M/nm`（repo map）`r{repeat}`，如 `nc_sx_nm_r0`。

> **repo_map 变量说明：** repo map 是新接入的消融变量（ADR-0016），用于验证"代码理解索引是否减少探索轮次"。当前表格数字为接入 repo_map 前的消融结果，接入后数值待真实 API 重跑验证。

**TaskResult**（`matrix.py:15-22`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| instance_id | str | 任务 ID |
| cell | EvalCell | 实验配置 |
| passed | bool\|None | True=通过，False=未通过，None=错误 |
| error | str\|None | 错误消息 |
| stats | dict | 各因变量的统计值 |
| trajectory_path | str | SQLite 数据库路径 |

---

## 5. FakeBackend

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

FakeBackend 始终返回 `TextBlock("ok")` + `stop_reason=end_turn`，不做任何推理。它的用途：

- **零 API 成本验证框架**：验证任务配置正确、矩阵展开正确、报告生成正确
- `--fake` flag 自动限制为 1 task × 1 cell，快速反馈

FakeBackend 可以让评测框架的 CI 在几秒内验证配置正确性，而不消耗真实的 API 调用。

---

## 6. 报告生成

**generate_report()**（`reporter.py:14-77`）产出两种表格：

**汇总表**——各配置的聚合对比（当前含 RepoMap 列）：

| 压缩 | 并发 | RepoMap | 通过率 | 平均轮次 | 平均 input tokens |
|------|------|---------|--------|----------|-------------------|
| ✗ | ✗ | — | 待真数据 | — | — |
| ✗ | ✓ | — | 待真数据 | — | — |
| ✓ | ✗ | — | 待真数据 | — | — |
| ✓ | ✓ | — | 待真数据 | — | — |

> ⚠️ v0.1 的 83%/93% 等示例数字基于假 pass/fail，已废弃。现行报告另含 pass^k、轨迹指标、失败模式分布三节；真数字待 20 题消融产出（`eval/README.md`）。

**逐任务细节表**——每个任务在每个配置上的具体表现：

| 任务ID | 配置 | 通过 | 轮次 | input tokens | run_end_reason |
|--------|------|------|------|--------------|----------------|
| django__django-10097 | nc_sx_nm_r0 | ✓ | 18 | 523,400 | end_turn |
| ... | ... | ... | ... | ... | ... |

**错误列表：** 列出执行过程中出现异常的任务和错误消息。

---

## 7. 当前结果

> ⚠️ 本节 v0.1 的 83%/93% 等数字基于**假 pass/fail**（`passed=True` 硬编码，验收测试未实跑），已废弃且不得引用。
> 2026-08 现状（详见 `docs/handoff/2026-08-03-vague-code-eval-system.md`）：

- **评测体系**：真验收（sanity gate 双检 + F2P/P2P 实跑）、pass^k、轨迹指标、LLM-as-Judge、八类失败分类全部落地
- **任务集**：31 题（全部 OpenAI SWE-bench Verified 官方保留）；17 道脏题已剔除；本机可跑 20 题（sympy 17 + sphinx 2 + pytest 1），sklearn/astropy 10 题因本机无 MSVC 待 Linux/CI
- **真数字**：待 20 题基线消融产出（`python -m eval.cli --tasks eval/tasks.json --max-turns 25 --repeat 3`）
- **单一实证**：合成任务端到端 verified=True（Agent 真修好 bug）、judge 5/5；真实任务验证了 verify 正确判 f2p:fail/timeout/no_diff

---

## 8. 添加新任务——攻略

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 从 SWE-bench Lite 选取官方保留题（`eval/select_verified_tasks.py`） | 沿用 `filter_out=False` 标注；node id 需在 base_commit 上验证 |
| 2 | 在 `eval/env.py` 的 `REPO_SETUP` 策展环境 | 依赖 wheel + PYTHONPATH 源码策略；编译守卫用 `sysmodules` 桩；按提交日期分版本 |
| 3 | 跑 sanity gate 验证判别器 | `uv run python scratch_curate.py <instance_id>`；F2P 断言失败 / P2P 通过 |
| 4 | 添加到 `tasks.json` + 人工筛查 | 打分见 `eval/audit_report.html` |
| 5 | 真实 API 单题验证 | `python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 1` |

---

## 下一篇

→ **T1: 你的第一个任务**：动手教程——从安装到跑通第一个 Agent 任务。

**相关链接：** eval/README.md（现行用法）、docs/plans/0016-eval-methods.md（设计）、docs/handoff/2026-08-03-vague-code-eval-system.md（全量总结）

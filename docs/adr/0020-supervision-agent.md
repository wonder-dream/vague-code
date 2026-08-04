---
status: accepted
date: 2026-08-05
---

# 0020: 终止机制从轮次预算迁移到 Supervision Agent + 护栏

## 背景

基线评测（20 题 × OFAT × k=3，$10.28）暴露：43% 失败是撞 `max_turns=25`，失败轨迹大量为 bash 探索循环（19-36 次 bash/run、0 次编辑）与假完成（no_diff：宣称完成但工作区零改动）。轮次预算的两种改法都不够——加大轮次只让空转者烧更多钱；硬停滞检测（连续 N 轮无进展即终止）统计武断，会把"深挖代码"误杀。

## 决策

1. **`max_turns` 从预算变为保险丝**：默认 500（config.py 已有 500 警告阈值），只拦截失控（真死循环），不管束正常任务；正常停止交给下述机制。
2. **新增 Supervision Agent**：主循环外以轻量单次 LLM 调用运行的监督者，周期性（每 6 轮）读取轨迹事件流 + 工作区状态，输出五值结构化评估（`on_track / off_track / needs_verification / stuck / done`）+ guidance 文本，经 `guidance_provider`（loop.py:171 已有通道）注入主循环。监督者无工具，职责是"看"与"说"，不是"做"。
3. **完成校验**：主 agent 声明 end_turn 时，监督者全局判定（工作区 diff 非空 ∧ 不触碰测试文件 ∧ 轨迹尾部测试类命令 exit 0）→ `done` 则 loop 直接终止，`reason="supervisor_done"`。
4. **停止权责**：`done` 直接终止；`stuck` 连续 2 次且期间零编辑才终止（`reason="stagnant"`）；其余评估只注入 guidance、不终止。
5. **测试结果结构化**：bash 工具解析测试输出（pytest 等，exit code 已存在），把"PASS/FAIL"结构化反馈给模型——完成判定的客观信号地基。
6. **监督模型**：默认与被评 Agent 同模型，`--supervisor-model` 可参数化（评测侧）。
7. **可审计**：监督者每次调用（输入转写 + 输出 JSON）落轨迹 `supervision` 事件，离线可重放、judge 可评监督者质量。
8. **默认关闭**：`SupervisionConfig.enabled=False`，评测时显式开启——未充分评测前不作为产品默认路径（与 ADR-0018 subagent 同策略）。

## Considered Options

- **纯轮次预算**（现状 25 轮）：简单，但 43% 超时 + 空转者烧满预算，方向已证伪。
- **硬停滞检测**（连续 N 轮无进展终止）：零 LLM 成本，但统计武断——"深挖代码"与"重复空转"在工具序列上不可分，误杀风险高。
- **Supervision Agent**（选定）：语义判断停滞/方向/完成，成本为主 run 的 5-10%；代价是实现复杂度与监督者自身的可靠性（以 `supervision` 事件落盘 + judge 评测兜底）。

## Consequences

- 轮次从"预算"变"保险丝"：正常 run 靠 `end_turn` / `supervisor_done` 停止，难题不再撞轮次。
- 空转与假完成在语义层被拦截（stuck 判停 / done 完成校验），而非统计猜测。
- 监督成本 ≈ 主 run 的 5-10%（40 轮 run ≈ 6-8 次监督调用，每次 4-6K tokens 输入）。
- `classify.py` 失败分类新增 `stagnant` / `supervisor_done` 语义（后者不算失败）。
- 评测基线口径变更：与 25/40 轮历史数据不可直接对比，需重新跑核心层定新基线。
- 关联实现：`docs/plans/0018-supervision-agent.md`；术语见 CONTEXT.md。

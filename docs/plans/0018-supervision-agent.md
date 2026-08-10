# 0018: Supervision Agent — 监督式终止与导航

EDD 第三轮迭代：基线 43% 失败是撞 max_turns（25 轮），且大量 bash 空转（19-36 次/run、0 编辑）与假完成（no_diff）。本轮把"终止机制"从轮次预算迁移到**监督 + 护栏**三层：保险丝（500 轮）→ 语义监督（Supervision Agent）→ 硬兜底（连续 stuck 判停）。决策记录见 `docs/adr/0020-supervision-agent.md`。

---

## 决策汇总（grill-with-docs 逐项定案）

| # | 决策点 | 定案 |
|---|--------|------|
| Q1 | max_turns 语义 | **500 保险丝**（config.py 已有 500 警告阈值；只拦失控不管束正常任务） |
| Q2 | 完成信号/停止机制 | **B 测试结果结构化 + D 停滞检测，C（验证强制提示）缓行**；后因"D 太武断"升级为监督方案 |
| Q3a | 监督者形态 | **轻量单次 LLM 调用**（仿 judge.py 的 `backend.complete`，无工具循环） |
| Q3b | 监督触发 | **周期（每 6 轮）+ 完成校验（end_turn 时）双触发** |
| Q4 | 监督输入 | **过程信号摘要（metrics.py 现成）+ 尾部轨迹转写（仿 judge._transcript_text，8-12 轮）+ 工作区 diff stat**；封顶 4-6K tokens/次；任务文本仅周期监督带 |
| Q5 | 监督输出 | **五值结构化 JSON**（on_track/off_track/needs_verification/stuck/done + guidance + evidence）；stop 权责：done 直停、stuck 连续 2 次且零编辑才停、其余只提示；JSON 解析失败重试 1 次后跳过 |
| Q6 | 监督模型 | **同模型 + `--supervisor-model` 参数化**（过程导航非终局打分，自我增强偏差风险低；成本敏感） |
| Q7 | 术语/ADR | CONTEXT.md 新增 **Supervision Agent** 术语；ADR-0020 已写 |

---

## 实现清单

### 1. 产品层

**`vague_code/agent/config.py`** — `SupervisionConfig` dataclass：
```python
@dataclass
class SupervisionConfig:
    enabled: bool = False        # 默认关闭（ADR-0020 #8）
    period: int = 6              # 周期轮数
    model: str | None = None     # None = 同主 agent 模型
    max_input_tokens: int = 6000
    stuck_limit: int = 2         # 连续判 stuck 次数（含零编辑判定）
    def validate(self): ...      # period >= 1
```
`AgentConfig.supervision: SupervisionConfig = field(default_factory=SupervisionConfig)`

**`vague_code/agent/loop.py`**：
- 主循环条件 `max_turns` 保持（500 保险丝），新增三处钩子：
  - 周期监督：`turn % config.supervision.period == 0` → `_run_supervision(...)`，输出经 guidance 注入（复用 loop.py:269 `_drain_guidance` 通道，监督输出 push 进 guidance 队列）
  - 完成校验：主 agent `end_turn` 分支 → `_run_supervision(..., mode="final")`，`done` 则直接 emit `run_end reason="supervisor_done"` 并终止；非 done 则注入 guidance 打回
  - stuck 累计：轨迹内统计（编辑事件 + 监督评估），连续 2 次 stuck 且零编辑 → emit `run_end reason="stagnant"`
- `_run_supervision`：构造监督输入（见 Q4）→ `backend.complete` 单次调用 → 解析五值 JSON（复用 judge `_extract_json` 模式）→ emit `EventType.supervision`（新事件类型：输入转写 + 输出 JSON + 消耗 tokens）→ 返回 (assessment, guidance)
- `run_end` reason 新值：`supervisor_done` / `stagnant`

**`vague_code/agent/ir.py`** — `EventType` 增 `supervision`

**`vague_code/agent/tools.py`** — bash 测试结果结构化：
- `_bash_factory` 返回里对测试类命令（`pytest` 前缀）追加一行解析：从输出提取 `N passed / M failed / X error`（pytest 格式）或 fallback exit code 判 PASS/FAIL
- 描述不变；结构化行格式：`[test] PASS (3 passed) | FAIL (1 failed)`——给模型明确信号

### 2. 评测层

**`eval/harness.py`**：
- `run_eval` 加 `--supervisor` 开关（默认 false），开启时 `AgentConfig.supervision.enabled=True`
- `_extract_stats` 统计 supervision 事件：`supervision_calls` / `supervision_tokens` / `supervision_cost`（成本并入 `cost_usd`）

**`eval/classify.py`**：
- `run_end_reason == "stagnant"` → 新类"停滞（监督判停）"
- `run_end_reason == "supervisor_done"` → 归入 success（verified=True 已覆盖；verified=False 的 supervisor_done 归 test_fail）
- 失败分布图新增停滞类

**`eval/metrics.py`**：
- `metrics_from_events` 处理 `supervision` 事件类型（计数 + 评估分布，喂 reporter 监督质量列）

### 3. 文档（已完成）

- `docs/adr/0020-supervision-agent.md` — 决策记录
- CONTEXT.md — Supervision Agent 术语

---

## 验收标准

1. [ ] 单元测试：`SupervisionConfig.validate`；`_run_supervision` 输入构造（含 token 封顶）；五值 JSON 解析（合法/非法/重试）；stuck 累计判停逻辑；完成校验 done 直停（FakeBackend 注入监督响应）
2. [ ] `--fake` 链路：监督开关开启不破坏 fake 冒烟（监督调用同样走 FakeBackend）
3. [ ] 3 题复测（16792/21612/24213 × k=1，40 轮 + 监督开）：≥2/3 过且无 regression；监督调用 4-8 次/run；`supervision` 事件落 db 可查
4. [ ] 失败分布：`stagnant` 类出现且占比 < 15%（监督应防止多数空转，而非制造新失败类）
5. [ ] 成本：3 题监督增量 < 主 run 成本的 15%

---

## 成本与风险

- 监督增量：40 轮 run ≈ 6-8 次监督调用 × 4-6K tokens ≈ 主 run 的 5-10%；3 题验证 ≈ +$0.10-0.20
- 风险：监督者误判（把深挖判 stuck）→ 以 `supervision` 事件落盘 + judge 抽样评测监督质量；评估分布进 reporter 可见
- 范围声明：监督默认关闭，产品日常不受影响；评测显式开启

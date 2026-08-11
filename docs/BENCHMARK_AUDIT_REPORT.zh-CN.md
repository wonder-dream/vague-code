# FirstCoder 测评体系审查报告

> 审查对象：`benchmark/` 目录、`firstcoder/cli.py` 的 benchmark 模式、`tests/test_harbor_adapter.py`、
> 已完成的 Harbor 运行包 `benchmark/runs/harbor/aider-polyglot-feedback-retry-20260726/2026-07-26__12-07-27/`。
>
> 审查方法：逐文件阅读实现与产物（config.json / lock.json / result.json / 运行报告 README.md / 设计 spec /
> 单元测试），并结合一次完整 225 题运行的统计结果进行交叉核对。
>
> 本报告第一部分是"这个项目怎么做测评"的事实梳理与评估，第二部分是**可迁移到其他 coding agent
> 测评设计的方法论清单**（第 5 章），可以直接作为其他项目设计测评时的参考框架。

---

## 1. 测评体系总览

### 1.1 一句话概括

FirstCoder 不自己实现任何数据集 runner，而是通过外部评测运行时 **Harbor**（v0.18.0）执行：
Harbor 负责"取题 → 起容器 → 跑 agent → 跑 verifier → 存产物"，FirstCoder 只提供一个
installed-agent 适配器 `benchmark.harbor.firstcoder_agent:FirstCoderHarborAgent`，外加一个
Aider 风格的"测试失败反馈修复"插件。

### 1.2 组件与职责

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| Harbor | 外部依赖（`pip install 'harbor==0.18.0'`，未列入 `pyproject.toml`） | 数据集解析、Docker 任务环境、verifier 执行、结果存储 |
| Agent 适配器 | `benchmark/harbor/firstcoder_agent.py` | 把当前 FirstCoder checkout 最小化 staging 进容器、装依赖、跑一条非交互指令 |
| 反馈修复插件 | `benchmark/harbor/aider_feedback_plugin.py` + `aider_feedback_trial.py` | 在 `reward=0` 后把 verifier 输出回灌同一 session 再修复一次 |
| Benchmark 模式 | `firstcoder/cli.py:run_benchmark_turn` | bypass 权限、关闭写前 review、换 benchmark 系统提示词、套用循环预算 |
| 测评报告 | `benchmark/runs/harbor/.../README.md` | 结果口径、错误分层、恢复命令、证据索引 |
| 单元测试 | `tests/test_harbor_adapter.py` | 适配器与插件的回归保护（`pytest.importorskip("harbor")`） |

### 1.3 一次测评的完整生命周期

```
Harbor 解析本地数据集(.local/harbor-datasets/aider-polyglot)
   ↓ 每个任务
1. 创建隔离 Docker 容器（任务工作目录 + 语言工具链）
2. Adapter install：staging pyproject.toml + README.md + firstcoder/（无 .git/.venv/.env）
   → 引导 Python 3.11+（apt/apk/dnf/yum 或 uv 兜底）
   → 独立 venv 安装（共享 pip/uv 缓存 /opt/firstcoder-cache，下载重试 3 次退避）
3. Adapter run：非交互 `firstcoder --benchmark --project . --message <题目指令>`
   → 生成 provider config.toml（环境变量注入，密钥不落盘）
   → 会话副本导出 /logs/agent/firstcoder-session.jsonl
4. Harbor 运行数据集自带 verifier → 写 reward.txt（1.0/0.0）
5. （可选 feedback plugin）若 reward=0 且任务为 shared-verifier 单步任务
   → 取 verifier 输出末 60KB → 同一 session --resume-session 再跑一轮 → 重新验证
6. 收集 agent/verifier 日志、时间、reward → result.json
```

---

## 2. 逐环节剖析

### 2.1 测评集：Aider Polyglot（225 题，6 语言）

- 分布：JavaScript 49 / Java 47 / Go 39 / Python 34 / Rust 30 / C++ 26。
- 形态：Exercism 风格"读既有代码 + 保持接口 + 实现并通过独立测试"，覆盖字符串、数学、数据结构、
  并发、网格搜索、业务规则、序列化、语言特性（所有权/生命周期/Gradle 工具链）等能力方向。
- 定位判断（准确）：这是"代码编辑 + 测试通过"型 benchmark，不是"聊天 + 思路"型，也不是纯算法
  （见运行报告第 4~6 章的能力分类）。

**评估**：数据集选型合理，与 agent 形态（本地编码 agent）匹配；但只有这一个数据集有完整运行结果，
对"真实仓库级软件工程能力"（SWE-bench 类）覆盖为零。

### 2.2 环境隔离与安装（做得好）

- **最小 staging**：只拷贝 `pyproject.toml`、`README.md`、`firstcoder/`，显式排除 `__pycache__`；
  不拷贝 `.git`、`.venv`、`.env`、本地 session —— 既防泄密也防"把答案带进考场"。
- **安装健壮性**：跨发行版引导 Python（apt/apk/dnf/yum）、uv 兜底、下载重试 3 次带退避、
  共享缓存挂载（`--mounts` 绑到 `/opt/firstcoder-cache`）减少并发重复下载、venv 每次重建避免并发污染。
- **平台差异**：Windows 需 Docker Desktop Linux 容器模式，文档有明确警告（先单题 `-n 1` 再扩并发）。

### 2.3 Agent 接入层（职责边界清晰）

- 适配器**不读取 verifier 文件**、不注入 hidden-test 信息 —— 由 Harbor 负责把指令传给 agent，
  代码中有注释和测试双重保证（`test_harbor_agent_builds_quoted_firstcoder_benchmark_command` 断言
  命令里不含 verifier 路径相关内容、不含 `api_key =` 明文）。
- 模型配置通过 `FIRSTCODER_PROVIDER_NAME/MODEL/BASE_URL/API_KEY` 环境变量注入，容器内动态生成
  `config.toml`，密钥只从宿主机环境取，不写入仓库（符合 AGENTS.md 安全要求）。
- 每次 trial 导出 `firstcoder-session.jsonl`，保证 agent 行为可回放。

### 2.4 Agent 侧 benchmark 模式（针对性提示词，好）

`firstcoder/cli.py:run_benchmark_turn` 做的事：

1. 权限切到 **bypass**、关闭写前 review（benchmark adapter 会显式关闭该事件）；
2. 设置 `benchmark_task` → 系统提示词从 `agent_instructions.md` 换成
   **`benchmark_agent_instructions.md`**（专为评测写的提示词，74 行）；
3. 套用 `AgentLoopLimits.swe_lite()`（60 轮 / 100 次 provider 调用 / 1800 秒），命令行可覆盖
   （实际运行用了 `--max-tool-rounds 120`）。

benchmark 提示词本身是**反作弊设计**的样板：
- 明确"verifier 是唯一真相"、禁止修改/禁用/弱化 verifier、隐藏测试、harness、评分脚本；
- 禁止联网搜索答案、禁止使用外部任务专属解集；
- 最小改动、不重构无关代码、不 commit、不 PR、不等待用户输入；
- 要求"可观察结果真正落盘/服务真正应答"才算完成，而不是自述完成。

### 2.5 评分与验证

- 评分完全交给**数据集自带 verifier**（黑盒）：写 `reward.txt` 为 1.0/0.0；agent 自述不算数。
- 每个 trial 保留 verifier 输出 `test-stdout.txt`，可逐题复盘。

### 2.6 交互协议：Aider 式反馈修复（有争议但明确）

`AiderFeedbackTrial` 实现"实现 → 独立验证 → 失败输出反馈 → 同 session 修复 → 再验证"闭环：

- 触发条件严格：**只有** `reward == 0`（或 C++ verifier 编译失败且无 reward 文件的特例）才给修复轮；
  超时、缺 reward 文件、provider 失败**不**触发；
- 反馈文本以 "The tests are correct. Do not modify the tests." 开头，并截断到 60KB；
- 通过 `--resume-session` 复用同一 session（同一上下文），不是新会话；
- 仅限 `shared` verifier 模式的单步任务；多步任务、separate-verifier 任务走 Harbor 默认行为；
- 实现方式是 job 生命周期内 monkeypatch `Trial.create`，job 结束恢复。

**评估**：协议边界（什么时候允许反馈、反馈什么、哪些失败不算）定义得清楚，且明确写"Terminal-Bench
等不允许 test-feedback 的 benchmark 禁用此插件"。这是本 repo 测评设计中最值得学习的点之一。

### 2.7 指标口径（诚实且分层）

运行报告同时给出两个指标并明确区分：

| 指标 | 计算 | 含义 |
| --- | --- | --- |
| reward-only pass@1 | 213 / 221 = **96.38%** | 拿到明确 0/1 判分的题里通过率（模型代码能力口径） |
| Harbor 端到端 Mean | 213 / 225 = **94.67%** | 无 reward 异常按 0 计入（整条链路的成功率口径） |

- `pass_at_k = {}`（未算）、token 计数与成本 `null`（未跟踪）。
- 报告反复声明：96.38% **不是** Aider 官方 leaderboard 分数，不能声称超越/排名任何模型；
  并给出与官方榜的逐维度差异表（任务集/agent/模型路由/交互/异常计分/基础设施）。

### 2.8 可复现性与证据链（强项）

运行包三件套：
- `config.json`：完整固化运行配置（并发、重试、agent kwargs、env、数据集路径）；
- `lock.json`：Harbor 版本 + 每个任务的 sha256 digest + 解析后的完整环境/verifier 配置
  （版本锁定：verifier/task-cache 变更会改变 lock，防止混用不兼容产物）；
- `result.json`：逐题 reward 明细 + exception_stats + 重试计数；
- 加上 `job.log`、单题 agent/verifier 日志、`README.md` 报告。

恢复命令也是证据链的一部分：`harbor job resume -f <异常类型>` 定向恢复，保留已通过题与原产物。

### 2.9 异常分类与恢复（最重要的方法论）

运行中把失败分成**互斥类别**并分别处理：

| 类别 | 本次数量 | 处理 |
| --- | --- | --- |
| `reward=0`（明确失败） | 8 | 进通过率分母；其中 6 个还叠加了 agent 非零退出，需逐题看日志再归因 |
| `RewardFileNotFoundError`（无 reward 文件） | 4 | 本次全部是 C++ verifier 编译失败未写 reward——语义上属实现错误但被计为异常 |
| `NonZeroAgentExitCodeError` / 网络 / 超时 | 6 | 基础设施类，重试 2 次仅覆盖 `NonZeroAgentExitCodeError`，其余定向 resume |
| Harbor 自动/人工恢复累计 | 77 次 | 含 Docker 网络地址池耗尽、Gradle 代理、provider 波动 |

关键纪律（AGENTS.md + 报告）：
- 不把基础设施错误解释成模型能力；
- 恢复前确认没有其他 `harbor run` / `harbor job resume` 进程（防锁竞争）；
- resume 只针对相关异常类型，看完 verifier 日志再归因；
- `RewardFileNotFoundError` 与网络错误分账。

### 2.10 单元测试（对测评代码本身的测试）

`tests/test_harbor_adapter.py`（20+ 用例）覆盖：命令构造与引号转义、config 生成可解析、
反馈轮只跟真实失败、C++ 编译失败特例判定、多步任务不劫持、插件恢复 Harbor 原工厂、
staging 不含 .env/__pycache__、安装命令含共享缓存与重试、Python 引导脚本各分支。
测试用 `pytest.importorskip("harbor")` —— Harbor 未装则跳过（见风险 3.2-4）。

---

## 3. 审查发现

### 3.1 做得好的地方（值得其他项目抄）

1. **职责边界**：agent 项目不实现数据集 runner；评测运行时、容器、verifier、存储全部外包，
   自己的代码只有薄薄一层适配器 —— 单点维护、可随 Harbor 升级。
2. **反作弊卫生**：最小 staging + 不读 verifier + 提示词禁止碰 harness + 首轮对 verifier 盲。
3. **错误分层**：reward=0 / 无 reward / 网络 / 超时 / 非零退出分账，报告不混为一谈。
4. **双指标口径**：reward-only 与端到端 mean 分开报，并明确各自"能说什么、不能说什么"。
5. **证据链**：config/lock/result/日志四件套 + 恢复命令，满足"可复跑、可仲裁"。
6. **诚实声明**：明确"非官方榜分数"，不暗示模型排名。
7. **反馈修复协议有纪律**：什么失败可以修复、修复什么内容、哪些 benchmark 禁用，写死并测试。
8. **对测评代码写测试**：适配器/插件有独立单测，防回归。

### 3.2 问题与风险（按严重度）

**P1（会影响结论可信度）**

1. **统计单薄**：单次运行、n=1、无方差/置信区间、无多次采样；`pass_at_k={}` 未启用。
   → 96.38% 只能视为"一次配置下的点估计"，不能外推到"该 agent 的真实能力"。
2. **基础设施噪声污染端到端指标**：77 次重试 / 225 题（34%），`result.json` 显示 10 个 errored；
   6 个 `reward=0` 与 `NonZeroAgentExitCodeError` 混叠 —— 端到端 mean 的"含噪性"虽然报告解释了，
   但任何对外引用都有误读风险。
3. **C++ 特例是补丁式逻辑**：`should_request_feedback_after_missing_reward` 用字符串匹配
   "CMake build failed" + "error:" 判定"无 reward 的编译失败可修复"。这是对数据集 verifier 缺陷
   （编译失败不写 reward.txt）的补偿，脆弱且与具体 verifier 耦合；同类逻辑将来遇到别的数据集会失效。
4. **成本/效率完全未测**：token、cache token、cost 全部 null。对于要对比"编辑协议/token 成本"
   （报告自己引用了 Aider 的成本与 edit-format 指标）的团队这是明显缺口。

**P2（工程稳健性）**

5. **Harbor 未列入 dev 依赖**：`pyproject.toml` 只有 `dev = ["pytest"]`；测试 `importorskip` 会导致
   Harbor 缺失时静默跳过全部测评测试（CI 里可能毫无感知地失去覆盖率）。
6. **monkeypatch 依赖 Harbor 私有实现**：`Trial.create` 与 `Trial.__dict__` 属内部 API，
   Harbor 0.18 → 0.19 若改名/改签名即静默失效或炸掉；无版本约束声明（README 只写了 `harbor==0.18.0` 命令）。
7. **数据集补丁不可复现**：Java Gradle 代理补丁只存在于 Git 忽略的 `.local/harbor-datasets/`，
   仓库内无法重建同一数据形态；AGENTS.md 的"lock 会随 verifier/task-cache 变更更新"只防混用，
   不解决"别人拿不到同一个数据集补丁"的复现缺口。
8. **文档平台摩擦**：运行命令以 `zsh -lic`、`/bin/zsh` 写死（连单元测试都 `executable="/bin/zsh"`），
   Windows 需要额外适配；README 中的 shell 示例混合了 bash/zsh 语法。
9. **数据集分类文档是一次性快照**：`harbor-datasets-all-classification.md` 标注抓取日期，属参考资料，
   无维护机制会过期。

**P3（体验/可选）**

10. 无 dashboard/趋势化：历史多次运行之间没有自动比对（diff 报告），版本回归要手工看。
11. 单题日志（agent/verifier）以文件形式存在，未做结构化索引（如 SQLite/parquet），
    数百题级复盘要靠 grep。
12. 未接入 CI：整包运行需 24h+，仓库没有 nightly 或 smoke 级流水线（只推荐了手工"先单题"流程）。

### 3.3 对当前结果的一句话结论

> 96.38% reward-only / 94.67% end-to-end 是一次**配置受控、证据完整、口径诚实**的单次点估计；
> 它证明"FirstCoder 在本地锁定配置下能完成 Aider Polyglot 225 题中的 213 题（获得有效判分的 221 题中）"，
> 不足以支撑"超过某模型/某榜单"的任何主张 —— 报告本身正是这么声明的，这一点做得对。

---

## 4. 测评设计的方法论（可迁移到其他 coding agent 测评）

> 这一章从 FirstCoder 的做法中提炼出**通用原则**，供其他 coding agent 项目设计测评时直接采用。

### 4.1 架构原则

| # | 原则 | 说明（含本 repo 证据） |
| --- | --- | --- |
| A1 | **Agent 项目不实现 runner** | 数据集解析/容器/verifier/存储交给外部评测运行时（Harbor）；自己只有 adapter 与协议插件。职责单点化，升级、审计、替换都容易。 |
| A2 | **以"一次非交互 turn"为唯一入口** | agent 暴露一个无 UI 的 benchmark 入口（`firstcoder --benchmark`），评测只调用它；交互层（TUI）与评测层严格分离。 |
| A3 | **最小化 staging，考场不携带杂物** | 只拷贝运行必需文件；.git/.venv/.env/本地 session 一律不带。既防泄密也防作弊。 |
| A4 | **配置全部参数化** | 模型、provider、endpoint、API key、并发、轮数、超时、推理强度全部 CLI/环境变量注入；密钥经环境变量透传，落盘 config 用占位符。 |
| A5 | **安装与启动要能自举** | 跨发行版引导运行时（Python 引导 + uv 兜底 + 包管理器检测）、下载重试带退避、共享下载缓存。基础设施不稳时不要让它变成模型失败。 |

### 4.2 反作弊与完整性（按此清单自查）

- [ ] agent 首轮看不到 verifier 文件、hidden tests、评分脚本；
- [ ] 系统提示词显式禁止：修改/删除/弱化 verifier、搜索公开答案、外部解集、改动无关文件；
- [ ] 提示词要求"可观察结果真正存在"才算完成（文件落盘、服务应答），禁止自述完成；
- [ ] 需要反馈修复时，反馈文本禁止暗示"可以改测试"（本 repo 用 "The tests are correct..." 前缀）；
- [ ] staging 白名单复制（列出允许拷贝的内容），并有测试断言不含 .env/__pycache__。

### 4.3 错误分层与指标口径（评测报告最容易被误读的地方）

1. **建立失败分类学**，互斥穷尽：
   `reward=0`（明确失败）｜无 reward 文件｜agent 非零退出｜网络/provider 错误｜agent 超时｜verifier 超时。
2. **主指标与口径分离**：
   - 主指标 = 拿到明确判分的题目上的通过率（模型能力口径）；
   - 端到端 mean（含异常按 0）只用于反映"整条链路成功率"，不单独作为能力分数；
   - 报告必须写清楚每个数字的分母。
3. **奖励归因纪律**：reward=0 与异常混叠的题目必须逐题看日志再归因，报告里标注"待复核"而不是猜。
4. **诚实声明边界**：本地运行的分数 ≠ 官方 leaderboard 分数；明确列出与官方口径的差异维度
   （任务集/agent/模型路由/交互协议/异常计分/基础设施）。

### 4.4 交互协议（若 benchmark 允许反馈修复）

- [ ] 只在**协议明确允许**反馈修复的 benchmark 启用该机制（本 repo 明确禁止用于 Terminal-Bench 等）；
- [ ] 触发条件必须收窄：仅真实 `reward=0`；超时/缺文件/网络错误一律不触发；
- [ ] 反馈内容截断有上限（60KB），复用同一 session（同一上下文），标注这是"第几轮修复"；
- [ ] 特例补偿逻辑（如 C++ 无 reward 编译失败）要独立成函数、带单测，并标注其数据集耦合性。

### 4.5 证据链与可复现性（发布任何数字前必须有）

| 产物 | 内容 |
| --- | --- |
| `config.json` | 完整运行配置（并发/重试/kwargs/env/数据集） |
| `lock.json` | 运行时版本 + 任务内容 hash（sha256）+ 解析后的环境/verifier 配置 |
| `result.json` | 逐题 reward + 异常统计 + 重试计数 |
| 单题日志 | agent 输出 + 会话 JSONL + verifier 输出 |
| 报告 README | 指标口径、错误分层、恢复命令、证据索引、生成时间 |
| 恢复命令 | `resume -f <异常类型>` 可定向恢复，保留已通过 trial |

原则：**任何会对外宣称的数字，都必须能从这个包里重建或仲裁**。

### 4.6 运维纪律（长跑前的检查单）

- [ ] 跑前确认没有其他 run/resume 进程（锁竞争）；
- [ ] Docker 网络清理（地址池耗尽先 `docker network prune -f`）；
- [ ] 语言工具链的网络代理要显式传给 JVM/包管理器（Gradle 不读 HTTP_PROXY 的坑）；
- [ ] 先单题 + `-n 1` 验证 agent 日志与 verifier 结果，再扩并发；
- [ ] 凭据只放 Git 忽略的 `.env.harbor`，不进 README/历史/命令。

### 4.7 对测评代码本身写测试

适配器命令构造、反馈触发条件、提示词内容、staging 白名单、插件恢复等都要有单测；
用 `importorskip` 可选依赖时，要避免"依赖缺失导致静默丢覆盖率"（见 3.2-5）。

### 4.8 一份"最小可行测评设计"清单（新项目可逐条勾选）

1. 选定与 agent 形态匹配的数据集（编辑型/仓库型/终端型），记录版本与内容 hash；
2. 搭建隔离执行环境（容器），agent 以最小 staging 进入；
3. 定义唯一非交互入口，配置参数化、密钥环境变量化；
4. 写 benchmark 专用系统提示词（反作弊条款）并有测试断言关键句存在；
5. 定义失败分类学 + 主指标/端到端指标口径 + 报告声明模板；
6. 确定交互协议（是否允许反馈修复，允许则按 4.4 收窄触发条件）；
7. 固化 config/lock/result/日志四件套，编写恢复命令；
8. 先小规模冒烟（1 题、n=1）再全量；
9. 明确数字边界声明（不与官方榜混淆）；
10. 补 token/cost 统计（本 repo 目前缺，建议新项目一开始就接）。

---

## 5. 对 FirstCoder 的改进建议（按优先级）

| 优先级 | 建议 |
| --- | --- |
| P1 | 接 token/cost 统计（result.json 中对应字段目前全 null），报告补充每题 token 与成本分位； |
| P1 | 多次采样（至少 n=3）或提供方差/置信区间，避免"单次点估计"被过度解读； |
| P1 | 把 C++ 特例判定升级为"verifier 退出码 + 输出存在性"的结构化判定，或直接修复本地数据集 verifier 写 reward.txt，减少字符串匹配耦合； |
| P2 | 把 `harbor==0.18.0` 纳入 dev 依赖并加版本下界，删掉 importorskip 静默跳过（改为显式 skip 并打警告）； |
| P2 | 为 monkeypatch 的 Harbor 内部 API 增加版本断言/升级探针（如检查 `Trial.create` 签名），防止 Harbor 升级后静默失效； |
| P2 | 将数据集补丁（Gradle 代理等）以 patch 文件形式版本化，与 `.local/` 缓存分离，保证他人可复现同一数据形态； |
| P2 | CI 增加 smoke 级评测流水线（单题 + `-n 1`，非 24h 全量），防止 adapter 漂移； |
| P3 | 多运行之间的自动 diff 报告（reward 变化、异常类别变化）；单题日志结构化索引。 |

---

## 附录：证据文件索引

| 证据 | 路径 |
| --- | --- |
| Harbor 接入说明 | `benchmark/harbor/README.md` |
| Agent 适配器实现 | `benchmark/harbor/firstcoder_agent.py` |
| 反馈修复插件 | `benchmark/harbor/aider_feedback_plugin.py`、`aider_feedback_trial.py` |
| Benchmark 系统提示词 | `firstcoder/context/prompts/benchmark_agent_instructions.md` |
| Benchmark 入口与预算 | `firstcoder/cli.py`（`run_benchmark_turn`、`_benchmark_limits`）、`firstcoder/agent/loop_limits.py` |
| 设计决策 | `docs/superpowers/specs/2026-07-21-harbor-only-benchmark-design.md` |
| 完成运行报告 | `benchmark/runs/harbor/aider-polyglot-feedback-retry-20260726/2026-07-26__12-07-27/README.md` |
| 运行配置/锁/结果 | 同上目录 `config.json`、`lock.json`、`result.json` |
| 适配器单元测试 | `tests/test_harbor_adapter.py` |
| 数据集目录快照 | `benchmark/harbor/harbor-datasets-all-classification.md` |

（审查日期：2026-08-10）

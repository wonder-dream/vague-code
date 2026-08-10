# 0040: 评测体系重构 — Aider Polyglot + Docker 容器化 + FirstCoder 方法论

- **日期**: 2026-08-10
- **状态**: approved（用户确认：数据集选 A Aider Polyglot + 尝试 Docker 安装）

## 背景与决策

- 参考文档：`docs/BENCHMARK_AUDIT_REPORT.zh-CN.md`（FirstCoder 测评体系审查报告）
- 放弃现有评测设计（自建 SWE-bench runner + 31 题任务集），按报告方法论重做
- 用户确认：
  1. 方法论落地到自建 runner（不引 Harbor，但引入 **Docker 容器隔离**）
  2. 数据集选 **A：Aider Polyglot**（Exercism 风格"读代码+保持接口+实现+独立测试通过"，6 语言 225 题）
  3. 尝试安装 Docker（本机已具备全部前置：winget 1.29 / WSL2+Ubuntu-22.04 / 虚拟化已启用）

## 现状盘点

**保留（已实证资产）**：`env.py`（venv 缓存，迁移进容器）、`verify.py`（sanity gate 双检/防钻空子/状态隔离）、`metrics.py`、`judge.py`、`classify.py`（口径对齐）、12 个评测测试文件

**废弃/重构**：`harness.py`/`reporter.py` 旧指标口径（无错误分层、单一 pass rate）；SWE-bench 31 题任务集退役

## 新设计（对照报告第 4 章）

| # | 报告原则 | 落地 |
|---|---|---|
| 1 | A2 唯一非交互入口 | `vague-code --benchmark`（chat 非交互变体）：bypass 权限、关写前 review、专用提示词、预算上限（轮数/秒数） |
| 2 | 反作弊提示词 | `benchmark_agent_instructions.md`：verifier 唯一真相、禁改测试/禁搜答案/禁碰 harness、"可观察结果真正落盘才算完成" + 单测断言关键句 + 命令不含 verifier 路径 |
| 3 | 失败分类学（4.3） | 互斥穷尽：`verified / reward=0 / no_diff / f2p_fail / env_broken / infra(无reward·网络·超时·非零退出)` |
| 4 | 双指标口径（4.3） | 报告三列：**pass@1（有效判分题分母）+ e2e mean（全题含异常按 0）+ pass^k** + "非官方榜"声明模板 |
| 5 | 证据链四件套（4.5） | 每 run 固化 `config.json`（完整运行配置/镜像版本）+ `lock.json`（任务内容 sha256 + venv 依赖指纹）+ `result.json`（逐题 reward/异常/重试/cost）+ `--resume -f <分类>` 定向恢复 |
| 6 | token/cost 统计（3.2-4） | 每题 input/output/**cache-hit** 分位 |
| 7 | pass@k（3.2-1） | `--tries` 多次尝试 → pass_rate_1/2（对标 Aider 报告字段） |
| 8 | CI smoke（3.2-12） | GitHub Action：单题 + `--fake` 冒烟 |
| 9 | 对评测代码写测试（4.7） | 现有 12 文件扩展：benchmark 入口/提示词/分类/证据链 |
| 10 | 运维纪律（4.6） | 文档化：先单题 `-n 1` 再全量；凭据环境变量透传不落盘 |

## 数据集（Aider Polyglot）

- 仓库：`https://github.com/Aider-AI/polyglot-benchmark`（公开）
- 结构：`{cpp,go,java,javascript,python,rust}/exercises/practice/<exercise>/`（题目描述 + 骨架 + tests/）
- 225 题：JS 49 / Java 47 / Go 39 / Python 34 / Rust 30 / C++ 26
- 官方要求容器运行（LLM 生成代码不受审查直执行）→ 容器隔离执行：每任务起容器 → 最小 staging → 跑 agent → verifier → 收集
- 各语言 verifier（容器内）：pytest / node / go test / mvn test / cargo test / g++ 编译+跑

## 执行顺序

0. **Docker 安装与验证**：`winget install Docker.DockerDesktop`（UAC）→ WSL2 后端 → `docker version` 连通 → hello-world 冒烟；失败预案：`wsl --update` 后重试，仍失败回退"本机直跑 Python 34 题子集 + 安全声明"
1. `--benchmark` 无交互入口 + 反作弊提示词 + 单测
2. 失败分类学 + 双指标口径重构（harness/reporter/classify 对齐）
3. 证据链三件套 + 定向恢复
4. token/cost 分位 + `--tries` pass@k
5. CI smoke workflow
6. polyglot-benchmark 克隆 + 任务加载适配器 + 各语言容器 verifier
7. 单题 n=1 真跑验证 verifier → Python 34 题子集 → 全 6 语言

## 验证标准

- 全量测试绿 + ruff/mypy 零错误；fake 冒烟跑通 `--benchmark`
- Docker daemon 连通，容器内单题 verifier 判定正确（对标 Aider pass_rate 口径）
- 报告含：双指标三列 + 错误分类分账 + 证据链索引 + cost 分位

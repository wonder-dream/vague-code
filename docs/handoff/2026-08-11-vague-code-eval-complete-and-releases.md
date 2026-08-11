# Handoff 2026-08-11：评测体系重构完成（Polyglot 225 题 100%）+ 发布 0.1.11→0.1.17 + 分词器三族齐备 + effort 参数

> 本会话完成四件大事：**① Aider Polyglot 评测体系全链路重构并实测 225 题 pass@1=100%**（ADR-0040，参考 FirstCoder 测评审计报告）；**② 发布 0.1.11→0.1.17 共 7 个版本**（含 3.11 兼容、安装链路修复）；**③ 三族分词器齐备**（DeepSeek/GPT/Claude）；**④ `--effort` 推理努力参数**（实测成本差 16 倍）。工作区干净，全部已提交推送。

---

## 一、评测体系重构（ADR-0040，主交付）

参考 `docs/BENCHMARK_AUDIT_REPORT.zh-CN.md`（FirstCoder 测评审计报告方法论），自建 runner 落地（本机无 Docker Desktop，用 **WSL2 Ubuntu 原生 dockerd**）。

### 基础设施
- **Docker**：WSL2 Ubuntu-22.04 dockerd 28.4（systemd + 代理 drop-in `HTTP_PROXY=http://127.0.0.1:7897` + 禁 IPv6）；Windows 侧经 `wsl -d Ubuntu-22.04 -u root -- docker` 调用
- **vague-eval 镜像**（`eval/docker/Dockerfile`）：python3.10+pytest / node 20 / go / rust 1.97 / openjdk-17 / g+++cmake / libboost-dev；构建须 `--network host --build-arg HTTP_PROXY=...`

### 评测链路（`eval/polyglot.py`）
- agent 宿主跑（benchmark 反作弊提示词）→ verify 前恢复源测试（防改测试）→ 清 build 残留（防 CMakeCache 污染）→ 容器内跑语言 verifier（挂载点=exercise 名目录，cpp 依赖）→ exit 0 = verified
- 依赖缓存挂载：js `/root/npm-cache`、java `/root/gradle-cache`（WSL 侧持久目录）
- 跑法：`python -m eval.polyglot --dataset <polyglot-benchmark 路径> --repeat 1 --max-turns 40 --out polyglot_final_v5.md`
- 数据集：`git clone https://github.com/Aider-AI/polyglot-benchmark`（6 语言 225 题）

### 实测结果
- **225 题 pass@1 = 224/224 = 100%**（e2e 99.56%，$13）；唯一剔除 cpp/complex-numbers（**数据集缺陷**：官方 example 同样编译失败——原版测试 unused static 函数撞 -Werror）
- 对抗注入 5/5 拦截（rm -rf/.env 读取/越权写/curl|sh/chmod -R 777，safe 权限）

### Verifier 修复链（7 个坑，全沉淀进代码）
| 坑 | 修复 |
|---|---|
| CMakeCache 污染（agent 宿主 mingw 构建残留） | `_clean_build_artifacts` verify 前删 build/ |
| Boost 1.74 config mode 误判 date_time | `-DBoost_NO_BOOST_CMAKE=ON` |
| cpp 产物名 = 目录名（连字符保留） | `./build/{exercise}` |
| node 12 太老（jest 29 需 ≥14） | 镜像升 node 20（nodesource） |
| gradlew CRLF shebang（Windows autocrlf） | `_fix_shebang_line_endings` |
| Gradle 不读 HTTP_PROXY | JVM 系统属性（GRADLE_OPTS） |
| `npm test`（jest ./*）匹配 agent 自建 debug spec | `npx jest <exercise>.spec.js` 只跑题目 spec |

### 方法论落地（eval/ 模块）
- `classify.py`：互斥失败分类学（success/f2p_fail/p2p_fail/no_diff/gaming_tests/timeout/env_broken/infra/…；verify:fail→f2p_fail、dataset_defect→env_broken）
- `reporter.py`：双指标口径 pass@1（有判分题）+ e2e（全题）+ pass^k + pass@k（Aider 口径）+ cost/token 分位 + 非官方榜声明
- `evidence.py`：证据链三件套 config/lock/result（`runs/eval/run_*/`），`--regen` 离线重建报告
- cli `--resume-fail <分类>` 定向恢复
- 对抗注入（`harness.py` task_type=adversarial 分流）
- judge 审计数字：SWE 20 样本 exact 55%/within-1 65%（代理人工分口径）
- gold 轨迹：已归档（metrics 从未实现有 gold 指标）
- CI test.yml 加 eval fake 冒烟

### 关键文件
- 正式报告：`polyglot_final_v5.md`；证据链：`runs/eval/results_final_225_v5.json` + `run_*` 目录
- 计划：`.opencode/plans/0040-eval-redesign.md`；0016 已标注退役

## 二、发布序列 0.1.11 → 0.1.17（全部 PyPI 已发布）

| 版本 | 内容 |
|---|---|
| 0.1.11 | 0039 会话级模型切换 + 0040 benchmark 入口/评测体系 |
| 0.1.12 | **requires-python 3.12→3.11**（修复 Python 3.11 机器装不上） |
| 0.1.13 | README uv 安装优先 |
| 0.1.14 | **memory.py f-string 反斜杠 3.11 兼容**（PEP 701 是 3.12 语法，3.11 导入即炸）+ TUI flaky CI 根治 |
| 0.1.15 | **tiktoken 移可选 extra `[gpt]`**（regex 依赖在部分环境解析失败） |
| 0.1.16 | **Claude 专用分词器 `[claude]`**（anthropic-tokenizer 65K 词表） |
| 0.1.17 | `--effort low/high` + `/` 浮层滚动窗口 + 焦点收起 |

## 三、分词器现状（三族齐备）

| 模型族 | 分词器 | 位置 |
|---|---|---|
| DeepSeek | deepseek_tokenizer（官方同源） | 硬依赖 |
| GPT | tiktoken o200k/cl100k | `[gpt]` extra |
| Claude | anthropic-tokenizer（官方 65K BPE） | `[claude]` extra |

**Claude 对齐未实测**：本机中转（code.newcli.com/claude）token 数据不可信（messages usage 恒 1、count_tokens 中文失真 3 倍）；基于"Anthropic 从未更换词表"公开事实接入。对齐脚本已备（`count_tokens` 端点对比），待官方 key 或真实 usage 端点验证。

## 四、`--effort low/high`（v0.1.17）

- **实测**：deepseek-v4-flash 支持 `reasoning_effort`（low 零思考 input 5 vs high 84 tokens，差 16 倍）；`effort=`/`thinking=` 参数名无效
- 接入：AgentConfig.reasoning_effort + call_config 透传 + deepseek/openai codec 白名单；CLI 四处入口；anthropic 无 effort 保持默认
- 用法：`vague-code --effort low "简单任务"`（省 token）/ `--effort high`（深推理）

## 五、待办（下次会话）

| 优先级 | 事项 | 说明 |
|---|---|---|
| **P1** | 中转站 UA 适配 | code.newcli.com/claude 只放行 `claude-cli/` User-Agent（SDK 直连 400"暂不支持"）；vague-code AnthropicBackend 需加自定义 UA 才能直连该中转；用户 foxcode key 可用（`sk-ant-oat01-...`，**已暴露在对话中，建议轮换**） |
| P1 | Claude tokenizer API 对齐实测 | 拿到真实 usage 的 key 后跑 `count_tokens` 对比脚本（临时脚本在 temp，可重建：messages/count_tokens 端点 + UA=claude-cli/1.0.66） |
| P2 | repeat≥3 采样 | 验证 100% 稳定性（~$40，报告 3.2-1 建议） |
| P2 | TUI 遗留 | 侧边栏/TUI 交互无已知未决 bug；两个旧 flaky 已根治（CI Linux 验证过） |
| P3 | 密钥轮换 | 用户决定暂缓（foxcode 中转 key 余额少） |

## 六、环境备忘（本机）

- **Docker**：WSL2 Ubuntu-22.04 dockerd（`wsl -d Ubuntu-22.04 -u root -- docker ...`）；代理 127.0.0.1:7897（Windows 侧代理软件）；镜像加速器 daemon.json 已配（实际靠代理）
- **评测数据**：polyglot-benchmark 克隆在 `C:\Users\VAGUE-~1\AppData\Local\Temp\opencode\polyglot-benchmark`（临时目录，重跑需重新 clone）
- **API key**：项目 `.env`（DEEPSEEK_API_KEY）；foxcode 中转 key 未落盘（仅本次会话临时使用）
- **发布流程**：改版本号 → `git tag v0.1.x` → push → CI（版本一致性校验 + pytest 全量 + twine check + trusted publishing）

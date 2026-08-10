# What Is a Coding Agent?

**谁需要读：** 所有对 Coding Agent 感兴趣的人，零编程基础可读
**前置阅读：** 无
**读完能做什么：** 理解 vague-code 是什么、能做什么、和 Copilot/ChatGPT 的区别

---

## 1. 一句话说清楚

Coding Agent（编码智能体）是一种能独立完成编程任务的 AI 程序。你告诉它要做什么，它自己读代码、写代码、跑测试，最后给你一份完整的汇报。

一个类比帮你理解：它不是帮你补全代码的助手，而是能独立完成任务的新同事。

- **Copilot** = 你写代码，它帮你补全下一行
- **ChatGPT** = 你问问题，它给你答案
- **Coding Agent** = 你布置任务，它从需求到验证独立完成

vague-code 就是一个 Coding Agent 的具体实现——一个轻量级的、可在本地命令行运行的编码智能体。

## 2. 和 Copilot/ChatGPT 的区别

三种模式在多个维度上有本质差别：

| 维度 | 补全模式（Copilot） | 问答模式（ChatGPT） | 自主模式（Coding Agent） |
|------|-------------------|-------------------|----------------------|
| 交互方式 | 你写代码，它补全 | 你提问，它回答 | 你布置任务，它独立完成 |
| 自主程度 | 低——你全程主导 | 中——一问一答 | 高——自主拆解/执行/验证 |
| 工具使用 | 无 | 无（ChatGPT 有插件但受限） | 读/写/搜索/命令/测试 |
| 工作记忆 | 当前文件 | 会话上下文 | 五层压缩 + 跨会话记忆 |
| 典型场景 | 编码时的行级补全 | 问答、explain、debug | 修 Bug、加功能、重构、评测 |

自主模式的核心工作流程：

```
任务 → 拆解 → 搜索 → 读取 → 修改 → 验证 → 汇报
```

每个步骤 Agent 都会自主决定需要什么工具、读取什么文件、执行什么命令，最终交付可以验证的结果。

## 3. 一次完整任务的旅程

让我们用一个真实的案例来看 vague-code 是如何完成一个任务的。

**任务：** "修一下 stats.py 的除零 bug"

```
  你                              vague-code
  │                                │
  ├─ "修一下 stats.py 的除零 bug" ─→│
  │                                │
  │                                ├─ [搜索] grep 搜索除以位置
  │                                │    → 找到 bug：分母未经零值检查
  │                                │
  │                                ├─ [读取] 查看 stats.py 全文
  │                                │    → 确认 bug 代码段
  │                                │
  │                                ├─ [分析] 判断修复方案
  │                                │    → 加 if denominator == 0 检查
  │                                │
  │                                ├─ [修改] 执行 patch 精确修改
  │                                │    → 在第 15 行插入零值检查
  │                                │
  │                                ├─ [验证] 运行 pytest
  │                                │    → 测试通过 ✓
  │                                │    （也可能失败，回到修改步骤重试）
  │                                │
  │                                └─ "修好了，加了零值检查" ←──┤
  │
  ✓ 完成
```

每个步骤都对应 Agent 使用的具体工具和产生的输出。搜索阶段用 `grep` 工具查找问题位置，读取阶段用 `read_file` 工具确认上下文，修改阶段用 `patch` 工具做最小改动，验证阶段用 `bash` 执行测试。

这个案例可以在 `tests/_target_bug/vague_code/stats.py` 中找到真实代码。

## 4. Agent 的能力清单

vague-code 提供了 7 项核心能力，每一项对应一个工具（tool）：

| 能力 | 对应工具 | 一句话示例 |
|------|---------|-----------|
| 读代码 | `read_file` | "看看 vague_code/main.py 的内容" |
| 写代码 | `write_file` | "创建一个新的模块文件 utils.py" |
| 精确修改 | `patch` | "把第 23 行的 pass 改为 continue" |
| 搜索文件 | `glob` | "找到所有 test_*.py 文件" |
| 搜索内容 | `grep` | "搜索 add_user 函数在哪里定义" |
| 执行命令 | `bash` | "运行 pytest 看看测试是否通过" |
| 定位符号 | `code_search` | "calculate 函数定义在哪个文件哪一行？" |
| 跨会话回忆 | `memory_search` | "用户之前提到过什么偏好？" |

这些工具的调用受到一套内置规则的约束。最重要的两条守则：

1. **先读后改**——Agent 不会凭空修改它没读过的文件
2. **改完要测**——Agent 被要求每次修改后验证结果是否正确

这两条行为守则由系统提示（System Prompt）强制执行（`context.py:11-16`），是 Agent 可靠性的基础保障。

## 5. vague-code 的特别之处

vague-code 有五个独特的设计点，每一个都解决一个真实工程问题：

### 上下文压缩（Context Compression）

自动压缩旧对话，长会话不丢信息。就像你看书时自动做笔记，不会翻到后面忘了前面。

> **例子：** 20 轮对话后 Agent 仍然记得你最开始提到的需求，因为旧内容已经被智能压缩而非直接丢弃。

vague-code 使用五层压缩流水线：stale_snip（删被覆盖的旧读取）→ microcompact（折叠超长输出）→ structured_snip（轨迹驱动结构化压缩，零 LLM 成本）→ auto_compact（全量会话摘要）→ truncation（硬截断兜底）。详见 06-context-engineering.md。

### 权限系统（Permission System）

四级安全模式保护你的代码。危险操作要先问你"可以吗？"

> **例子：** Agent 想删除文件 → 弹窗确认 → 你按 Y 才放行。你也可以设置持久规则："bash rm 永远不需要确认"。

权限系统分 safe/normal/autoedit/auto 四种模式，按操作可逆性切分信任等级。每次决策记录审计日志，可随时回查。详见 07-permission-system.md。

### 跨会话记忆（Memory System）

Agent 记住你的偏好和历史解决方案，下次自动使用。

> **例子：** 昨天你告诉它"用 pytest"，今天再次使用时，它自动调用 pytest 而不需要再说一遍。

记忆为按需检索的 episodic 注入策略（跨会话记忆库 + `memory_search` 工具）。详见 08-memory-system.md。

### 工具并发（Tool Concurrency）

多个不冲突的操作同时执行，速度更快。

> **例子：** Agent 需要同时搜索 3 个不同的文件——如果互不冲突，vague-code 会并行执行，而不是一个个来。

并发调度基于冲突可串行化（Conflict Serializability）模型，保证并行效果与顺序执行一致。详见 05-tool-system.md。

### 可评测（Evaluation Harness）

跑分验证每个设计决策，数据驱动改进。

> **例子：** 消融实验可控制变量验证每个设计决策（如并发调度、上下文压缩）对 pass rate / token 消耗的实际影响。2026-08 起评测升级为真验收（sanity gate 双检 + F2P/P2P 实跑），数字口径见 `docs/handoff/2026-08-03-vague-code-eval-system.md`。

vague-code 内置评测框架，支持消融实验（Ablation Experiment），可控制变量验证每个设计选择。详见 12-evaluation-harness.md。

## 6. 你需要准备什么

要在本地运行 vague-code，你需要：

- **Python 3.12+**：`python --version` 验证
- **uv 包管理器**：`pip install uv`
- **API Key**：DeepSeek（https://platform.deepseek.com/api_keys）或 Anthropic
- **一个项目**：你想让 Agent 帮助改进的 Git 仓库

安装命令：

```bash
git clone <vague-code-repo-url>
cd vague-code
uv sync
echo 'DEEPSEEK_API_KEY=sk-xxx' > .env
```

安装完成后，运行第一个任务：

```bash
vague-code "列出当前目录的文件"
```

## 7. 总结

vague-code 是一个自主模式的 Coding Agent——它能独立完成编程任务，而不仅仅是一个代码补全或问答工具。它背后的核心子系统包括：Agent Runtime（主循环引擎）、Tool System（与世界交互的接口）、Context Engineering（上下文管理）、Permission System（安全防护）、Memory System（跨会话记忆）、Model Abstraction Layer（LLM 统一接入）和 Evaluation Harness（评测验证）。

接下来的文档将逐个深入这些子系统：从术语表（01）和架构总览（02）开始，到一次完整的单轮循环（03），再到每个子系统的专题深潜（04-12）。

**下一篇推荐：** 01-terminology.md——了解 vague-code 的核心术语，后续文档将直接使用。

**相关链接：** README.md（项目总览）、CONTEXT.md（术语规范）、ADR-0001（Agent 即库设计决策）

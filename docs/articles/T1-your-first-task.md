# T1：你的第一个任务

**谁需要读：** 第一次使用 XClaw 的开发者
**前置阅读：** 00-what-is-a-coding-agent.md（概念理解）
**读完能做什么：** 在本地跑起来 XClaw，完成第一个简单任务，会读运行记录

---

## 1. 环境准备

### 前提检查清单

```bash
python --version   # 需要 Python 3.12+
uv --version       # 需要 uv 包管理器
```

如果没有 uv：`pip install uv`

### API Key

前往 https://platform.deepseek.com/api_keys 获取 API Key。

### 克隆与安装

```bash
git clone <xclaw-repo-url>
cd xclaw
uv sync
```

预期输出：

```
Resolved 42 packages in 1.2s
  3  packages audited in 0.1s
```

### 验证安装

```bash
python -c "from src.agent.loop import Agent; print('OK')"
```

输出 `OK` 即安装成功。

### 设置 API Key

两种方式，推荐第一种：

```bash
# 方式 1：.env 文件（推荐）
echo 'DEEPSEEK_API_KEY=sk-xxxx' > .env

# 方式 2：环境变量（Windows PowerShell）
$env:DEEPSEEK_API_KEY = "sk-xxxx"
```

---

## 2. 第一个命令

### 任务：列出当前目录文件

```bash
python -m src.cli "列出当前目录下的文件"
```

输出分段解释（--verbose 模式将展示更多细节）：

```
[Model: deepseek-v4-flash]           ← MessageStart
[Thinking: ...]                       ← 推理过程（默认折叠）
[read_file 目录结构...]               ← ToolUseStart
[glob] **.*                          ← ToolUseStart
[Tool result: src\ README.md ...]     ← ToolResult
[Thinking: ...]                       ← 再次推理
[end_turn]                            ← MessageEnd：Agent 完成
Run {id} finished, reason: end_turn  ← 运行摘要
```

注意 Agent 的行为模式：先试图读目录结构 → 用 glob 搜索文件 → 汇总结果 → 结束。它展示了"先读后说"的守则。

### 第二个任务：创建并运行文件

```bash
python -m src.cli "创建一个 hello.py 文件，内容为 print('Hello')，然后运行它"
```

观察 Agent 的操作序列：
1. `write_file` 创建 `hello.py`
2. `bash python hello.py` 运行
3. 返回输出 `Hello`

与第一个任务对比：这里涉及多个工具的协同工作，展示了 Agent 的任务拆解能力。

### 对比 --no-stream 模式

```bash
python -m src.cli --no-stream "列出文件"
```

输出一次性打印，无流式效果。适合在管道或日志场景使用。

---

## 3. 常用参数实验

### 修改最大轮次

```bash
python -m src.cli --max-turns 3 "复杂的多步骤任务"
```

观察：3 轮耗尽后 Agent 结束，pending tool calls 被记录。对比默认 20 轮的输出差异——记得有些任务确实需要多轮迭代才能完成。

### 切换模型

```bash
python -m src.cli --model deepseek-v4-pro "分析项目结构"
```

用 `--verbose` 验证模型名是否正确传递。

### 指定工作目录

```bash
python -m src.cli "分析这个项目" /path/to/other/project
```

SystemPrompt 中的 `工作目录根路径` 会变为对应路径。Agent 的 read/write 操作都会被限制在这个目录下。

### 其他参数

```bash
# 关闭重试
python -m src.cli --no-retry "任务"

# 设置超时（秒）
python -m src.cli --timeout-s 60 "任务"

# 导出轨迹 JSONL
python -m src.cli --export-jsonl my_trajectory.jsonl "列出文件"
```

`--export-jsonl` 会在当前目录生成 `my_trajectory.jsonl` 文件，内含一次运行的完整事件流。

---

## 4. 读 JSONL 轨迹

### 用 Python 分析轨迹

```python
import json
import collections

with open("my_trajectory.jsonl") as f:
    events = [json.loads(line) for line in f]

print(f"总事件数: {len(events)}")
print(f"事件类型分布: {collections.Counter(e['type'] for e in events)}")

# 计算总 token
token_total = sum(
    e['payload'].get('usage', {}).get('input_tokens', 0)
    for e in events if e['type'] == 'llm_response'
)
print(f"总 input tokens: {token_total:,}")
```

### 事件流示例

前几行事件看起来像这样：

```
{"run_id": "abc...", "type": "run_start", "turn": null, ...}
{"run_id": "abc...", "type": "turn_start", "turn": 0, ...}
{"run_id": "abc...", "type": "compression", "turn": 0, ...}
{"run_id": "abc...", "type": "llm_response", "turn": 0, ...}
{"run_id": "abc...", "type": "tool_call", "turn": 0, ...}
```

### 关键统计

```python
total_turns = sum(1 for e in events if e['type'] == 'turn_start')
total_tool_calls = sum(1 for e in events if e['type'] == 'tool_call')
end_reason = next(
    e['payload']['reason'] for e in reversed(events)
    if e['type'] == 'run_end'
)
```

---

## 5. 探索 TUI 模式

### 启动 TUI

```bash
python -m src.cli tui "浏览项目结构"
```

TUI 布局（纯文字描述）：

```
┌───────────────────────────────────────────────────┐
│  Conversation View (75%)      │  Sidebar (25%)    │
│  ┌──────────────────────────┐ │  ┌──────────────┐ │
│  │ [model] deepseek-v4      │ │  │ History      │ │
│  │ [thinking] ... (可折叠)  │ │  │ 2024-12-01   │ │
│  │ [Tool] read_file         │ │  │ 2024-12-02   │ │
│  │ [Result] src/...         │ │  │              │ │
│  │                          │ │  │ Memory       │ │
│  │                          │ │  │ pytest ✓     │ │
│  └──────────────────────────┘ │  └──────────────┘ │
├───────────────────────────────────────────────────┤
│  Status Bar: ● Running  Turn 5/20  In: 12K Out: 3K│
├───────────────────────────────────────────────────┤
│  /                                                │
└───────────────────────────────────────────────────┘
```

### 首次交互练习清单

1. 按 **T** → 折叠/展开 thinking 块（看推理过程）
2. 按 **Tab** → 导航到 tool result 块
3. 按 **E** → 展开/折叠 tool result
4. 按 **/** → 聚焦命令输入 → 输入 `/help` 回车
5. 按 **/** → 输入 `/mode auto` → 切换到最大自动模式
6. 按 **Ctrl+C** → 停止当前运行的 Agent
7. 输入新任务（非斜杠命令）→ 重新启动 Agent
8. 按 **/quit** → 退出 TUI

---

## 6. 常见问题

| 问题 | 诊断 | 解决 |
|------|------|------|
| `DEEPSEEK_API_KEY not found` | 环境变量未设置 | 检查 `.env` 文件位置 |
| `ConnectionError` / 超时 | 网络问题 / API Key 无效 | 检查 API Key 是否正确 |
| Agent 卡住不动 | 等待 LLM 响应或工具超时 | Ctrl+C 停止，降低 `--max-turns` |
| 输出乱码 | 终端编码 | Windows Terminal 运行 |
| 权限拒绝 | normal 模式默认确认 | 按 Y 放行 / 加到 permission-rules |
| `ModuleNotFoundError` | 未安装依赖 | `uv sync` |

---

## 下一篇

→ **T2：修一个真实 Bug**：观察 Agent 完整的修 Bug 流程。

**相关链接：** 04-agent-runtime.md、README.md

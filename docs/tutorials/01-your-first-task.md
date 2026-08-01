# 细纲：01-your-first-task.md

**预估行数：** ~300 行
**定位：** 第一篇动手教程，让读者在本地跑起来 XClaw。

---

## 开头

- **谁需要读：** 第一次使用 XClaw 的开发者
- **前置阅读：** 00-what-is-a-coding-agent.md（概念理解）
- **读完能做什么：** 在本地跑起来 XClaw，完成第一个简单任务，会读运行记录

---

## 细纲

### 1. 环境准备（~40 行）

**前提检查清单：**
- Python 3.12+：`python --version`
- uv 包管理器已安装：`uv --version`
- DeepSeek API Key 已获取（https://platform.deepseek.com/api_keys）

**克隆与安装（含输出示例）：**
```bash
git clone <xclaw-repo-url>
cd xclaw
uv sync
```

**预期输出：**
```
Resolved 42 packages in 1.2s
  3  packages audited in 0.1s
```

**验证安装：**
```bash
python -c "from src.agent.loop import Agent; print('OK')"
```
→ 输出 `OK`

**设置 API Key（两种方式）：**
```bash
# 方式 1：.env 文件（推荐）
echo 'DEEPSEEK_API_KEY=sk-xxxx' > .env

# 方式 2：环境变量（Windows PowerShell）
$env:DEEPSEEK_API_KEY = "sk-xxxx"
```

### 2. 第一个命令（~50 行）

**任务：列出当前目录文件**
```bash
python -m src.cli "列出当前目录下的文件"
```

**输出分段解释：**
```
[Model: deepseek-v4-flash]           ← MessageStart（verbose 模式）
                                      ← ThinkingStart
[Thinking: ...]                       ← ThinkingDelta（推理过程，默认折叠）
[read_file 目录结构...]               ← ToolUseStart 推理判断
[glob] **.*                          ← ToolUseStart：使用 glob 搜索
[Tool result: src\ README.md ...]     ← ToolResult：glob 返回
                                      ← ThinkingStart（再次推理）
[end_turn]                            ← MessageEnd：Agent 完成
Run {id} finished, reason: end_turn  ← --verbose 显示摘要
```

**第二个任务：创建并运行文件**
```bash
python -m src.cli "创建一个 hello.py 文件，内容为 print('Hello')，然后运行它"
```
- 观察 Agent 的操作序列：write_file（创建文件）→ bash `python hello.py`（运行）→ 返回输出 `Hello`
- 与第一个任务对比：涉及多个工具的协同工作

**对比 --no-stream 模式：**
```bash
python -m src.cli --no-stream "列出文件"
```
- 输出一次性打印，无流式效果

### 3. 常用参数实验（~60 行）

**修改最大轮次：**
```bash
python -m src.cli --max-turns 3 "复杂的多步骤任务"
```
- 观察：3 轮耗尽后 Agent 结束，pending tool calls 被记录
- 对比默认 20 轮的输出差异

**切换模型：**
```bash
python -m src.cli --model deepseek-v4-pro "分析项目结构"
```
- `--verbose` 验证模型名是否正确

**指定工作目录：**
```bash
python -m src.cli "分析这个项目" /path/to/other/project
```
- SystemPrompt 中的 `Workspace root` 变为对应路径

**关闭重试：**
```bash
python -m src.cli --no-retry "任务"
```

**设置超时：**
```bash
python -m src.cli --timeout-s 60 "任务"
```

**导出轨迹 JSONL：**
```bash
python -m src.cli --export-jsonl my_trajectory.jsonl "列出文件"
```
- 生成 `my_trajectory.jsonl` 文件

### 4. 读 JSONL 轨迹（~50 行）

**用 Python 分析轨迹：**
```python
import json

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

**事件流示例（前 5 行打印）：**
```
{"run_id": "abc...", "type": "run_start", "turn": null, ...}
{"run_id": "abc...", "type": "turn_start", "turn": 0, ...}
{"run_id": "abc...", "type": "compression", "turn": 0, ...}
{"run_id": "abc...", "type": "llm_response", "turn": 0, ...}
{"run_id": "abc...", "type": "tool_call", "turn": 0, ...}
```

**关键统计：**
- 总轮次 = `sum(1 for e in events if e['type'] == 'turn_start')`
- 工具调用 = `sum(1 for e in events if e['type'] == 'tool_call')`
- 终止原因 = 最后一条 `run_end` 的 `payload.reason`

### 5. 探索 TUI 模式（~40 行）

**启动 TUI：**
```bash
xcode tui "浏览项目结构"
```

**ASCII 布局 mermaid（纯文字描述代替）：**
- 顶部横线分隔
- 左边 75% = Conversation View（流式输出、折叠块）
- 右边 25% = Sidebar（会话列表、记忆面板）
- 底部 = Status Bar（状态/轮次/token/压缩/模式）
- 最底部 = Command Input

**首次交互练习清单：**
1. 按 `T` → 折叠/展开 thinking 块（看推理过程）
2. 按 `Tab` → 导航到 tool result 块
3. 按 `E` → 展开/折叠 tool result
4. 按 `/` → 聚焦命令输入 → 输入 `/help` 回车
5. 按 `/` → 输入 `/mode auto` → 切换到最大自动模式
6. 按 `Ctrl+C` → 停止当前运行的 Agent
7. 输入新任务（非斜杠命令）→ 重新启动 Agent
8. 按 `/quit` → 退出 TUI

### 6. 常见问题（~30 行）

| 问题 | 诊断 | 解决 |
|------|------|------|
| `DEEPSEEK_API_KEY not found` | 环境变量未设置 | 检查 `.env` 文件位置（需与命令同级目录） |
| `ConnectionError` / 超时 | 网络问题 / API Key 无效 | 检查 `https://platform.deepseek.com/api_keys` |
| Agent 卡住不动 | 等待 LLM 响应或工具超时 | `Ctrl+C` 停止，降低 `--max-turns` |
| 输出乱码 | 终端编码 | Windows Terminal 运行 / 加 `chcp 65001` |
| 权限拒绝 | normal 模式默认确认 | 按 `Y` 放行 / 加到 `.agent/permission-rules.json` |
| `ModuleNotFoundError` | 未安装依赖 | `uv sync` |

---

## 结尾

**下一篇推荐：** → T2：修一个真实 Bug（观察 Agent 完整的修 Bug 流程）
**相关链接：** 04-agent-runtime.md、README.md

---

## 本文件说明

这是文档 `01-your-first-task.md` 的细纲（大纲）。实际写作时需在项目目录下手动运行每个命令以获取真实输出示例。JSONL 分析部分需实际创建一个轨迹文件做示例。

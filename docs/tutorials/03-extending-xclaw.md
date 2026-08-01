# 细纲：03-extending-xclaw.md

**预估行数：** ~500 行
**定位：** 展示如何扩展 XClaw 的三种能力。

---

## 开头

- **谁需要读：** 想为 XClaw 添加自定义功能的开发者
- **前置阅读：** 05-tool-system.md（工具系统）、09-model-abstraction.md（模型抽象）
- **读完能做什么：** 添加自定义工具、添加新厂商支持、添加自定义评测任务

---

## 细纲

**示例 1：添加 web_search 工具（~200 行）**

### 步骤 1：定义 Handler 函数（~40 行）

**创建 `src/agent/tools_web.py`：**

```python
from pathlib import Path
from typing import Callable
from src.agent.tools import Tool
from src.agent.ir import ToolSpec


def _web_search_factory(workdir: str) -> Callable[[dict], str]:
    """创建 web_search handler"""
    root = Path(workdir).resolve()

    def handler(input: dict) -> str:
        query = input.get("query", "")
        if not query:
            raise ValueError("query is required")

        import requests
        try:
            resp = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("RelatedTopics", []):
                if "Text" in item and "FirstURL" in item:
                    results.append(f"{item['Text']}\n{item['FirstURL']}")
            return "\n\n".join(results) if results else "No results found"

        except requests.RequestException as e:
            return f"Search failed: {e}"

    return handler
```

**设计约束强调：**
- 输入始终是 `dict`（LLM 侧 JSON Schema 校验）
- 输出始终是 `str`（超过 50K 自动被 `_truncate_tool_content()` 截断）
- 异常必须在 handler 内部捕获并返回友好字符串（不能抛到 Agent 循环）

### 步骤 2：定义 ToolSpec（~30 行）

```python
WEB_SEARCH_SPEC = ToolSpec(
    name="web_search",
    description="Search the web for information. Returns results with title and URL.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query",
            },
        },
        "required": ["query"],
    },
)
```

**ToolSpec 字段说明：**
- `name`：工具标识（必须与 registry key 一致，`loop.py:182-183`）
- `description`：LLM 决定何时调用此工具的文本依据
- `parameters`：JSON Schema 格式（type、properties、required）

**参考：** `tools.py:262-272` 中 read_file 的 ToolSpec

### 步骤 3：构造 Tool 实例并注册（~30 行）

```python
WEB_SEARCH_TOOL = Tool(spec=WEB_SEARCH_SPEC, factory=_web_search_factory)
```

**注册到 `tools.py:341-348`：**
```python
from src.agent.tools_web import WEB_SEARCH_TOOL

DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": ...,
    # ... 所有已有工具
    "web_search": WEB_SEARCH_TOOL,   # 新增
}
```

**验证注册：**
```bash
python -c "from src.agent.tools import DEFAULT_TOOLS; print(list(DEFAULT_TOOLS.keys()))"
```

### 步骤 4：更新并发 scope 提取器（~30 行）

**在 `concurrency.py:54-85` `_extract_scope()` 中添加：**
```python
if name == "web_search":
    return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.READ)
```

**设计选择：**
- scope_type=WORKSPACE → 与所有写操作串行（保守策略，保险但牺牲并发性）
- 优化方向：web_search 不影响本地文件，可标记为 scope_type=EXACT, path=""（不与任何本地路径冲突→完全并发）

### 步骤 5：编写测试（~40 行）

```python
# tests/test_tool_web.py
import pytest
from unittest.mock import patch, Mock
from src.agent.tools_web import _web_search_factory


def test_web_search_basic():
    """测试正常的网络搜索"""
    mock_response = Mock()
    mock_response.json.return_value = {
        "RelatedTopics": [
            {
                "Text": "Python (programming language) - Wikipedia",
                "FirstURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
            },
        ]
    }

    with patch("requests.get", return_value=mock_response):
        handler = _web_search_factory("/tmp")
        result = handler({"query": "python language"})
        assert "Python" in result
        assert "Wikipedia" in result


def test_web_search_empty_query():
    handler = _web_search_factory("/tmp")
    with pytest.raises(ValueError, match="query is required"):
        handler({"query": ""})


def test_web_search_network_error():
    with patch("requests.get", side_effect=requests.RequestException("timeout")):
        handler = _web_search_factory("/tmp")
        result = handler({"query": "test"})
        assert "timeout" in result
```

**运行测试：** `uv run pytest tests/test_tool_web.py -v`

---

**示例 2：添加 Gemini codec（~200 行）**

### 步骤 1：创建 `src/agent/codecs/gemini.py`（~50 行）

**编码侧 `encode_request()` 要点：**
- Gemini API 使用 `contents[]`/`parts[]` 模型
- `system_instruction` → 顶层字段
- `user` content → `parts[{text: ...}]`
- `assistant` content → `parts[{text: ...}, {functionCall: ...}]`
- `tool_result` → `parts[{functionResponse: ...}]`

**解码侧 `decode_response()` 要点：**
- `candidates[0].content.parts[]` → Blocks
- `finishReason` → StopReason 映射
- `usageMetadata` → NormalizedUsage

**流式解码器 `GeminiStreamDecoder` 要点：**
- Gemini 的 Streaming 使用 `candidates[0].content.parts[0].text` 增量
- 需要处理 `functionCall` 的流式参数

### 步骤 2：在 `backend.py` 注册（~40 行）

```python
class GeminiBackend:
    def __init__(self, api_key: str, timeout_s: float = 120.0):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel("gemini-pro")

    def complete(self, messages, tools=None, config=None) -> ModelResponse:
        body = gemini_encode(messages, tools, config)
        response = self._model.generate_content(**body)
        return gemini_decode(response.to_dict())

    def stream(self, messages, tools=None, config=None) -> Iterator[StreamEvent]:
        body = gemini_encode(messages, tools, config)
        decoder = GeminiStreamDecoder()
        for chunk in self._model.generate_content(**body, stream=True):
            yield from decoder.decode_chunk(chunk.to_dict())
        yield from decoder.flush()
```

**添加工厂函数（参考 `backend.py:94-99`）：**
```python
def create_gemini_backend(api_key: str, timeout_s: float = 120.0) -> GeminiBackend:
    return GeminiBackend(api_key=api_key, timeout_s=timeout_s)
```

### 步骤 3：注册上下文窗口（~20 行）

**在 `context_tokens.py:14-19` `CONTEXT_WINDOWS` 中添加：**
```python
CONTEXT_WINDOWS: dict[str, int] = {
    # ... 现有模型
    "gemini-pro": 128_000,
}
```

**更新 `should_skip_thinking()`：** Gemini 不发送 thinking → 返回 True

### 步骤 4：编写 golden transcript（~30 行）

```python
# tests/test_gemini_golden.py
import json
from src.agent.codecs.gemini import decode_response
from src.agent.ir import StopReason


def test_gemini_decode_golden():
    with open("tests/fixtures/gemini_response.json") as f:
        raw = json.load(f)

    response = decode_response(raw)

    assert response.stop_reason == StopReason.end_turn
    assert len(response.message.content) > 0
    assert any(b._type() == "text" for b in response.message.content)
    assert response.usage.input_tokens > 0
```

### 步骤 5：集成测试（~30 行）

```bash
# 端到端测试（需要实际 API Key）
python -m src.cli --provider gemini --model gemini-pro "hello" --max-turns 1
```

---

**示例 3：添加评测任务（~100 行）**

### 步骤 1：创建任务（~30 行）

**添加到 `eval/tasks.json`：**
```json
{
  "instance_id": "myrepo__my-bug-001",
  "repo": "myusername/myrepo",
  "base_commit": "abc123def456",
  "problem_statement": "Fix the division by zero error in calc.py",
  "FAIL_TO_PASS": [
    "uv run pytest tests/test_calc.py::test_divide_by_zero -v"
  ],
  "PASS_TO_PASS": [
    "uv run pytest tests/test_calc.py -v -k 'not test_divide_by_zero'"
  ]
}
```

### 步骤 2：编写 `verify.sh`（~30 行）

```bash
#!/bin/bash
set -e
WORKDIR="$1"
cd "$WORKDIR"
pip install -e ".[test]" 2>/dev/null

# Fail-to-Pass 测试
uv run pytest tests/test_calc.py::test_divide_by_zero -v

# Pass-to-Pass 测试（不回归）
uv run pytest tests/test_calc.py -v -k 'not test_divide_by_zero'
```

### 步骤 3-4：验证流程（~40 行）

```bash
# FakeBackend 验证框架
python -m eval.cli --tasks eval/tasks_test.json --fake

# 真实 API 单题验证
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 1

# 查看报告
cat eval_report.md
```

---

## 结尾

**下一篇推荐：** → T4：运行消融实验
**相关链接：** 05-tool-system.md、09-model-abstraction.md、12-evaluation-harness.md

---

## 本文件说明

这是文档 `03-extending-xclaw.md` 的细纲（大纲）。示例代码为示意，实际写作时需确保代码可运行。Gemini codec 的 golden transcript 测试需先录制一个真实响应作为 fixture。

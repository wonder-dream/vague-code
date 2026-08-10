# T3：扩展 vague-code

**谁需要读：** 想为 vague-code 添加自定义功能的开发者
**前置阅读：** 05-tool-system.md（工具系统）、09-model-abstraction.md（模型抽象）
**读完能做什么：** 添加自定义工具、添加新厂商支持、添加自定义评测任务

---

## 示例 1：添加 web_search 工具

### 步骤 1：定义 Handler 函数

创建 `vague_code/agent/tools_web.py`：

```python
from pathlib import Path
from typing import Callable
from vague_code.agent.tools import Tool
from vague_code.agent.ir import ToolSpec


def _web_search_factory(workdir: str) -> Callable[[dict], str]:
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

**设计约束：**
- 输入始终是 `dict`（JSON Schema 校验在 LLM 侧，handler 信任类型）
- 输出始终是 `str`（超过 50K 自动被 `_truncate_tool_content()` 截断）
- 异常必须在 handler 内部捕获并返回友好字符串——不抛到 Agent 循环

### 步骤 2：定义 ToolSpec

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

ToolSpec 的三个字段：`name`（与 registry key 一致）、`description`（LLM 决定何时调用的依据）、`parameters`（JSON Schema 格式）。

### 步骤 3：构造 Tool 实例并注册

```python
WEB_SEARCH_TOOL = Tool(spec=WEB_SEARCH_SPEC, factory=_web_search_factory)
```

在 `tools.py:341-348`` 的 `DEFAULT_TOOLS` 中添加：

```python
from vague_code.agent.tools_web import WEB_SEARCH_TOOL

DEFAULT_TOOLS: dict[str, Tool] = {
    "read_file": ...,
    # ... 所有已有工具
    "web_search": WEB_SEARCH_TOOL,   # 新增
}
```

验证注册：

```bash
python -c "from vague_code.agent.tools import DEFAULT_TOOLS; print(list(DEFAULT_TOOLS.keys()))"
```

`web_search` 应该出现在输出中。

### 步骤 4：更新并发 scope 提取器

在 `concurrency.py:54-85` `_extract_scope()` 中添加：

```python
if name == "web_search":
    return ResourceScope(path="", scope_type=ScopeType.WORKSPACE, op_type=OpType.READ)
```

这里选择了保守策略：WORKSPACE + READ 意味着 web_search 与所有写操作串行。优化方向：web_search 不影响本地文件，可以标记为 EXACT + path="" 以实现完全并发。

### 步骤 5：编写测试

```python
# tests/test_tool_web.py
import pytest
from unittest.mock import patch, Mock
from vague_code.agent.tools_web import _web_search_factory


def test_web_search_basic():
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

运行测试：

```bash
uv run pytest tests/test_tool_web.py -v
```

---

## 示例 2：添加 Gemini codec

### 步骤 1：创建 codec

`vague_code/agent/codecs/gemini.py` 需要实现三个核心函数：

**编码侧 `encode_request()`：** Gemini 的 API 结构不同于 OpenAI 和 Anthropic。它使用 `contents[]/parts[]` 模型：
- `system_instruction` → 顶层字段
- user content → `parts[{text: ...}]`
- assistant content → `parts[{text: ...}, {functionCall: ...}]`
- tool_result → `parts[{functionResponse: ...}]`

**解码侧 `decode_response()`：** 从 Gemini 响应中提取：
- `candidates[0].content.parts[]` → IR Blocks
- `finishReason` → StopReason 映射
- `usageMetadata` → NormalizedUsage

**流式解码器 `GeminiStreamDecoder`：** Gemini 的 Streaming 更简单——增量文本在 `candidates[0].content.parts[0].text` 中。需要处理 `functionCall` 的流式参数序列化。

### 步骤 2：在 backend.py 注册

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

添加工厂函数（参考 `backend.py:94-99`）：

```python
def create_gemini_backend(api_key: str, timeout_s: float = 120.0) -> GeminiBackend:
    return GeminiBackend(api_key=api_key, timeout_s=timeout_s)
```

### 步骤 3：注册上下文窗口

在 `context_tokens.py:14-19` `CONTEXT_WINDOWS` 中添加：

```python
CONTEXT_WINDOWS: dict[str, int] = {
    # ... 现有模型
    "gemini-pro": 128_000,
}
```

更新 `should_skip_thinking()`：Gemini 不发送 thinking content → 返回 True。

### 步骤 4：编写 golden transcript 测试

```python
# tests/test_gemini_golden.py
import json
from vague_code.agent.codecs.gemini import decode_response
from vague_code.agent.ir import StopReason


def test_gemini_decode_golden():
    with open("tests/fixtures/gemini_response.json") as f:
        raw = json.load(f)

    response = decode_response(raw)

    assert response.stop_reason == StopReason.end_turn
    assert len(response.message.content) > 0
    assert any(b._type() == "text" for b in response.message.content)
    assert response.usage.input_tokens > 0
```

fixture 文件 `tests/fixtures/gemini_response.json` 从一次真实的 Gemini API 调用中录制，固化在代码库中。

### 步骤 5：集成测试

```bash
# 端到端测试（需要实际 API Key）
python -m vague_code.cli --provider gemini --model gemini-pro "hello" --max-turns 1
```

---

## 示例 3：添加评测任务

### 步骤 1：创建任务

在 `eval/tasks.json` 中添加一条任务记录：

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

### 步骤 2：编写 verify.sh

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

### 步骤 3-4：验证流程

```bash
# FakeBackend 验证框架（零 API 成本）
python -m eval.cli --tasks eval/tasks_test.json --fake

# 真实 API 单题验证
python -m eval.cli --tasks eval/tasks.json --model deepseek-v4-flash --repeat 1

# 查看报告
cat eval_report.md
```

FakeBackend 验证确认配置正确，单题验证确认 Agent 在真实 API 下表现符合预期，最后产出的报告就是消融实验的数据基础。

---

## 下一篇

→ **T4：运行消融实验**：矩阵配置、FakeBackend 验证、真实 API 运行、报告解读。

**相关链接：** 05-tool-system.md、09-model-abstraction.md、12-evaluation-harness.md

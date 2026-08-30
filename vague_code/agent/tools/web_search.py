"""web_search 工具（plans/0019）：DuckDuckGo 零 key 搜索，动态注入。

- 权限分类 network（复用 permission.py 预留分类：SAFE 拒绝 / NORMAL 确认 / AUTO 放行）
- 动态注入（不在 DEFAULT_TOOLS，loop 按 config.web_search.enabled 注册）→ 评测零影响
- 后端可插拔（config.provider，当前仅 ddg）
- 网络经 httpx，尊重 HTTP_PROXY/HTTPS_PROXY 环境变量
"""

from __future__ import annotations

from vague_code.agent.tools.base import OpType, ScopeType, Tool
from vague_code.agent.trust import mark_untrusted

DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_LIMIT = 10


class WebSearchTool(Tool):
    name = "web_search"
    description = ("搜索网页并返回结果列表（标题/链接/摘要）。"
                   "当需要获取项目之外的最新信息、文档或外部知识时使用。")
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询"},
            "max_results": {"type": "integer", "description": f"最大结果数（默认 {DEFAULT_MAX_RESULTS}）"},
        },
        "required": ["query"],
    }
    permission = "network"
    op_type = OpType.READ
    scope_type = ScopeType.WORKSPACE

    def __init__(self, workdir: str, provider: str = "ddg", max_results: int = DEFAULT_MAX_RESULTS):
        super().__init__(workdir)
        self._provider = provider
        self._default_max_results = max_results

    def run(self, input: dict) -> str:
        query = input.get("query", "")
        if not query:
            return "需要提供搜索查询。"
        max_results = int(input.get("max_results", self._default_max_results) or self._default_max_results)
        max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))
        # #29：查询参数脱敏，防止把文件内容/密钥拼进搜索查询
        from vague_code.agent.redact import redact_secrets

        query = redact_secrets(query)
        if self._provider == "ddg":
            return mark_untrusted(_search_ddg(query, max_results), "web_search 结果")
        return mark_untrusted(f"[web_search] 不支持的 provider: {self._provider}", "web_search 结果")


def _search_ddg(query: str, max_results: int) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "[web_search] 缺少 ddgs 依赖，无法搜索。"
    try:
        results = list(DDGS().text(query, max_results=max_results))
    except Exception as e:
        return f"[web_search] 搜索失败（网络或服务不可达）: {e}"
    if not results:
        return "未找到相关结果。"
    lines = []
    for i, r in enumerate(results, start=1):
        title = (r.get("title") or "").strip()
        href = (r.get("href") or r.get("url") or "").strip()
        body = (r.get("body") or "").strip()
        lines.append(f"{i}. {title}\n   {href}\n   {body}")
    return "\n\n".join(lines)

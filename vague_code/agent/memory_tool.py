from __future__ import annotations

from vague_code.agent.ir import ToolSpec

MEMORY_SEARCH_SPEC = ToolSpec(
    name="memory_search",
    description="搜索所有历史会话中的相关内容、解决方案或用户偏好。"
                "当感觉当前上下文缺少项目历史、用户偏好或类似问题的解决方案时使用此工具。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "描述你想查找内容的搜索查询",
            },
        },
        "required": ["query"],
    },
)


def make_memory_search_handler(memory_store):
    def handler(input: dict) -> str:
        query = input.get("query", "")
        if not query:
            return "未提供查询内容。请提供搜索查询以搜索记忆。"
        results = memory_store.search(query, k=5)
        if not results:
            return "未找到相关记忆。"
        lines = [f"--- 记忆（置信度: {r['confidence']}）---\n{r['content']}" for r in results]
        return "\n\n".join(lines)
    return handler

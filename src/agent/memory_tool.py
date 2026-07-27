from __future__ import annotations

from src.agent.ir import ToolSpec

MEMORY_SEARCH_SPEC = ToolSpec(
    name="memory_search",
    description="Search across all past sessions for relevant context, solutions, or preferences. "
                "Use this when you feel the current context lacks information about "
                "the project's history, user preferences, or previous solutions to similar problems.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query describing what you are looking for",
            },
        },
        "required": ["query"],
    },
)


def make_memory_search_handler(memory_store):
    def handler(input: dict) -> str:
        query = input.get("query", "")
        if not query:
            return "No query provided. Please provide a query to search memory."
        results = memory_store.search(query, k=5)
        if not results:
            return "No relevant memories found."
        lines = [f"--- Memory (confidence: {r['confidence']}) ---\n{r['content']}" for r in results]
        return "\n\n".join(lines)
    return handler

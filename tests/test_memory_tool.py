from __future__ import annotations

from src.agent.memory import MemoryStore
from src.agent.memory_tool import make_memory_search_handler


def test_handler_returns_results() -> None:
    store = MemoryStore(":memory:")
    store.ingest("User prefers TypeScript for backend services.")
    handler = make_memory_search_handler(store)
    result = handler({"query": "TypeScript"})
    assert "TypeScript" in result
    assert "Memory" in result


def test_handler_empty_query() -> None:
    store = MemoryStore(":memory:")
    handler = make_memory_search_handler(store)
    result = handler({"query": ""})
    assert "No query provided" in result


def test_handler_no_results() -> None:
    store = MemoryStore(":memory:")
    store.ingest("Python is great.")
    handler = make_memory_search_handler(store)
    result = handler({"query": "Rust"})
    assert "No relevant" in result


def test_handler_multiple_results() -> None:
    store = MemoryStore(":memory:")
    store.ingest("Use pytest for testing.")
    store.ingest("Use ruff for linting.")
    handler = make_memory_search_handler(store)
    result = handler({"query": "testing"})
    assert "pytest" in result

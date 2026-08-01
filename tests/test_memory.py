from __future__ import annotations

import tempfile
from pathlib import Path

from src.agent.memory import MemoryStore


def _make_store() -> MemoryStore:
    return MemoryStore(":memory:")


def test_ingest_and_search() -> None:
    store = _make_store()
    assert store.ingest("The user prefers TypeScript over Python.", kind="episodic")
    results = store.search("TypeScript", k=5)
    assert len(results) >= 1
    assert "TypeScript" in results[0]["content"]


def test_ingest_duplicate() -> None:
    store = _make_store()
    assert store.ingest("duplicate content")
    assert not store.ingest("duplicate content")  # duplicate → False


def test_search_empty_query() -> None:
    store = _make_store()
    assert store.search("") == []


def test_ingest_empty_content() -> None:
    store = _make_store()
    assert not store.ingest("")
    assert not store.ingest("   ")


def test_recent_episodic() -> None:
    store = _make_store()
    store.ingest("Older episodic fact.", kind="episodic")
    store.ingest("Newer episodic fact.", kind="episodic")
    store.ingest("Not episodic.", kind="pinned")
    recent = store.recent(kind="episodic", limit=5)
    assert len(recent) == 2
    assert "Newer" in recent[0]["content"]  # newest first
    assert "Not episodic" not in " ".join(r["content"] for r in recent)


def test_recent_limits() -> None:
    store = _make_store()
    for i in range(5):
        store.ingest(f"fact {i}", kind="episodic")
    recent = store.recent(kind="episodic", limit=2)
    assert len(recent) == 2
    assert recent[0]["content"] == "fact 4"


def test_recent_empty_db() -> None:
    store = _make_store()
    assert store.recent(kind="episodic", limit=5) == []


def test_search_no_results() -> None:
    store = _make_store()
    store.ingest("Python is great for data science.")
    results = store.search("rust", k=5)
    assert results == []


def test_search_multiple_results() -> None:
    store = _make_store()
    store.ingest("Use pytest for testing.")
    store.ingest("Use ruff for linting.")
    results = store.search("testing linting", k=5)
    assert len(results) >= 2


def test_fts_escape_special_chars() -> None:
    store = _make_store()
    store.ingest("Memory with special query content.")
    # Query with special FTS5 characters should not crash
    results = store.search("special *) query {test} [ignore]", k=5)
    assert isinstance(results, list)


def test_persist_reload() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "memory.db")
        store1 = MemoryStore(db_path)
        store1.ingest("Test memory persistence.", kind="episodic")
        store1.close()

        store2 = MemoryStore(db_path)
        recent = store2.recent(kind="episodic", limit=5)
        assert len(recent) == 1
        assert "persistence" in recent[0]["content"]
        store2.close()


def test_search_like_wildcard_percent() -> None:
    store = _make_store()
    store.ingest("Completion: 100% done")
    store.ingest("Another: done")
    results = store.search("100%", k=5)
    assert len(results) >= 1
    assert "100%" in results[0]["content"]


def test_search_like_wildcard_underscore() -> None:
    store = _make_store()
    store.ingest("File: test_file.py")
    store.ingest("File: test_file2.py")
    results = store.search("test_file.py", k=5)
    assert len(results) >= 1
    assert "test_file.py" in results[0]["content"]


def test_fts_match_by_word() -> None:
    store = _make_store()
    store.ingest("Always use type hints.")
    results = store.search("type hints", k=5)
    assert len(results) >= 1

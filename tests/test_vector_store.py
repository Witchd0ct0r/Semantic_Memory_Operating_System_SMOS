from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str, type: str = "doc", tags: list[str] | None = None) -> MemoryObject:
    return MemoryObject(type=type, content=content, timestamp=datetime.utcnow(), tags=tags or [])


def test_store_returns_correct_id(store: VectorStore) -> None:
    memory = _mem("Python is a high-level programming language.")
    returned_id = store.store(memory)
    assert returned_id == memory.id


def test_count_increments_after_store(store: VectorStore) -> None:
    assert store.count() == 0
    for i in range(3):
        store.store(_mem(f"Entry {i}"))
    assert store.count() == 3


def test_query_on_empty_store_returns_empty(store: VectorStore) -> None:
    results = store.query("anything", k=5)
    assert results == []


def test_query_returns_result_fields(store: VectorStore) -> None:
    store.store(_mem("Machine learning uses neural networks for pattern recognition."))
    results = store.query("deep learning algorithms", k=1)
    assert len(results) == 1
    r = results[0]
    assert "id" in r
    assert "content" in r
    assert "distance" in r
    assert isinstance(r["distance"], float)
    assert "metadata" in r


def test_query_ranks_semantic_relevance(store: VectorStore) -> None:
    store.store(_mem("Database indexing improves query performance."))
    store.store(_mem("Python asyncio enables concurrent programming."))
    store.store(_mem("Kubernetes orchestrates containerized applications.", type="adr"))

    results = store.query("container orchestration deployment", k=3)
    assert len(results) == 3
    assert "Kubernetes" in results[0]["content"]


def test_query_k_capped_at_count(store: VectorStore) -> None:
    store.store(_mem("Only one entry."))
    results = store.query("one entry", k=100)
    assert len(results) == 1


def test_metadata_type_preserved(store: VectorStore) -> None:
    store.store(_mem("Use PostgreSQL for relational data.", type="adr", tags=["database", "adr"]))
    results = store.query("PostgreSQL database", k=1)
    assert results[0]["metadata"]["type"] == "adr"
    assert "database" in results[0]["metadata"]["tags"]


def test_distance_in_valid_range(store: VectorStore) -> None:
    store.store(_mem("Exactly the same text query."))
    results = store.query("Exactly the same text query.", k=1)
    assert results[0]["distance"] < 0.05


def test_persistence_across_instances(tmp_path: Path) -> None:
    data_path = tmp_path / "data"
    store1 = VectorStore(persist_path=data_path)
    memory = _mem("Persistent memory entry.")
    store1.store(memory)

    store2 = VectorStore(persist_path=data_path)
    assert store2.count() == 1
    results = store2.query("persistent memory", k=1)
    assert results[0]["id"] == memory.id


def test_store_verbatim_round_trip(store: VectorStore) -> None:
    key = store.store_verbatim("def foo():\n    return 42\n", label="snippet")
    result = store.retrieve_verbatim(key)
    assert result is not None
    assert result["content"] == "def foo():\n    return 42\n"
    assert result["label"] == "snippet"
    assert store.count() == 0  # verbatim must not touch FAISS


def test_retrieve_verbatim_missing_returns_none(store: VectorStore) -> None:
    assert store.retrieve_verbatim("nonexistent") is None

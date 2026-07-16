from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.semantic_tools import graph_query


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow())


def test_graph_query_resolves_bare_term_to_entity_node(store: VectorStore) -> None:
    store.store(_mem("The `JWT` token is validated by the middleware."))
    result = graph_query("JWT", store)
    assert result["start_id"] == "entity:JWT"
    assert result["count"] >= 1


def test_graph_query_finds_memory_via_entity(store: VectorStore) -> None:
    m = _mem("The `JWT` token is validated by the middleware.")
    memory_id = store.store(m)
    result = graph_query("JWT", store, max_hops=1)
    ids = {n["id"] for n in result["nodes"]}
    assert memory_id in ids


def test_graph_query_unknown_term_returns_empty(store: VectorStore) -> None:
    result = graph_query("NoSuchTermAnywhere", store)
    assert result["nodes"] == []
    assert result["count"] == 0


def test_graph_query_empty_term_is_invalid(store: VectorStore) -> None:
    result = graph_query("   ", store)
    assert result.get("status") == "skipped"


def test_graph_query_accepts_exact_memory_id(store: VectorStore) -> None:
    m = _mem("The `JWT` token is validated by the middleware.")
    memory_id = store.store(m)
    result = graph_query(memory_id, store)
    assert result["start_id"] == memory_id
    ids = {n["id"] for n in result["nodes"]}
    assert "entity:JWT" in ids

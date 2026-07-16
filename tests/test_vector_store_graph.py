from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow())


def test_store_creates_memory_node(store: VectorStore) -> None:
    memory = _mem("The `JWTValidator` class handles tokens.")
    store.store(memory)
    node = store.graph.get_node(memory.id)
    assert node is not None
    assert node["type"] == "memory"


def test_store_creates_entity_nodes_and_edges(store: VectorStore) -> None:
    memory = _mem("The `JWTValidator` class handles tokens.")
    store.store(memory)
    entity_node = store.graph.get_node("entity:JWTValidator")
    assert entity_node is not None
    assert entity_node["type"] == "entity"
    neighbors = store.graph.traverse(memory.id, max_hops=1)
    ids = {n["id"] for n in neighbors}
    assert "entity:JWTValidator" in ids


def test_shared_entity_links_two_memories(store: VectorStore) -> None:
    m1 = _mem("The `JWTValidator` class validates tokens.")
    m2 = _mem("`JWTValidator` is used by the auth middleware.")
    store.store(m1)
    store.store(m2)
    neighbors = store.graph.traverse(m1.id, max_hops=2)
    ids = {n["id"] for n in neighbors}
    assert m2.id in ids


def test_delete_removes_memory_node(store: VectorStore) -> None:
    memory = _mem("The `JWTValidator` class handles tokens.")
    store.store(memory)
    store.delete(memory.id)
    assert store.graph.get_node(memory.id) is None


def test_store_batch_wires_graph_for_every_item(store: VectorStore) -> None:
    memories = [_mem(f"The `Entity{i}` does things.") for i in range(3)]
    store.store_batch(memories)
    for i, m in enumerate(memories):
        assert store.graph.get_node(m.id) is not None
        assert store.graph.get_node(f"entity:Entity{i}") is not None


def test_delete_batch_removes_memory_nodes(store: VectorStore) -> None:
    memories = [_mem(f"The `Entity{i}` does things.") for i in range(3)]
    store.store_batch(memories)
    store.delete_batch([m.id for m in memories])
    for m in memories:
        assert store.graph.get_node(m.id) is None

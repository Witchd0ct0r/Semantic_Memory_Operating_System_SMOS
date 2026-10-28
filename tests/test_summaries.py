from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _store_one(store: VectorStore, content: str = "Alpha beta gamma content.") -> str:
    return store.store(MemoryObject(type="doc", content=content, timestamp=datetime.utcnow()))


def test_set_and_get_summary_roundtrip(store: VectorStore) -> None:
    memory_id = _store_one(store)
    store.set_summary(memory_id, "s", "parser-1")
    assert store.get_summaries([memory_id])[memory_id] == {"summary": "s", "source": "parser-1"}


def test_get_summaries_missing_ids_absent(store: VectorStore) -> None:
    memory_id = _store_one(store)
    assert store.get_summaries([memory_id, "nope"]) == {}


def test_set_summary_replaces_existing(store: VectorStore) -> None:
    memory_id = _store_one(store)
    store.set_summary(memory_id, "first", "a")
    store.set_summary(memory_id, "second", "b")
    assert store.get_summaries([memory_id])[memory_id] == {"summary": "second", "source": "b"}


def test_delete_memory_removes_summary(store: VectorStore) -> None:
    memory_id = _store_one(store)
    store.set_summary(memory_id, "s")
    assert store.delete(memory_id)
    assert store.get_summaries([memory_id]) == {}


def test_delete_batch_removes_summaries(store: VectorStore) -> None:
    ids = [_store_one(store, f"Distinct content number {i} here.") for i in range(2)]
    for i in ids:
        store.set_summary(i, "s")
    store.delete_batch(ids)
    assert store.get_summaries(ids) == {}


def test_summary_works_for_graph_node_ids(store: VectorStore) -> None:
    store.set_summary("doc-node-123", "document level summary", "parser-x")
    assert store.get_summaries(["doc-node-123"])["doc-node-123"]["summary"] == "document level summary"


def test_get_summaries_empty_list(store: VectorStore) -> None:
    assert store.get_summaries([]) == {}

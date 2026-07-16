from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str, **kwargs) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow(), **kwargs)


def test_store_persists_source_ids_and_confidence(store: VectorStore) -> None:
    memory = _mem("Merged summary content.", source_ids=["a", "b"], confidence=0.6)
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["source_ids"] == ["a", "b"]
    assert record["metadata"]["confidence"] == pytest.approx(0.6)


def test_store_defaults_source_ids_empty_and_confidence_one(store: VectorStore) -> None:
    memory = _mem("Plain content with no extra fields.")
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["source_ids"] == []
    assert record["metadata"]["confidence"] == pytest.approx(1.0)


def test_store_batch_persists_source_ids_and_confidence(store: VectorStore) -> None:
    memories = [_mem("Batch item one.", source_ids=["x"], confidence=0.9)]
    ids = store.store_batch(memories)
    record = store.get_by_uuid(ids[0])
    assert record["metadata"]["source_ids"] == ["x"]
    assert record["metadata"]["confidence"] == pytest.approx(0.9)

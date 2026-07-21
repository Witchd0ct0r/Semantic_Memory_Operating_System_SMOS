from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.lifecycle import LifecycleManager
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_dedup_skips_ingested_segments(store: VectorStore) -> None:
    ids = [
        store.store(MemoryObject(type="segment", content="Article 9(2)(b) governs consent.")),
        store.store(MemoryObject(type="segment", content="Article 9(2)(b) governs consent.")),
    ]
    for i in range(10):
        store.store(MemoryObject(type="doc", content=f"Unrelated filler fact number {i}."))

    manager = LifecycleManager(store)
    manager._store._new_ids = list(ids)
    manager._deduplicate()

    for uuid in ids:
        assert store.get_by_uuid(uuid) is not None


def test_dedup_still_removes_non_segment_near_duplicates(store: VectorStore) -> None:
    ids = [
        store.store(MemoryObject(type="doc", content="The service uses Redis for caching sessions.")),
        store.store(MemoryObject(type="doc", content="The service uses Redis for caching sessions.")),
    ]
    for i in range(10):
        store.store(MemoryObject(type="doc", content=f"Unrelated filler fact number {i}."))

    manager = LifecycleManager(store)
    manager._store._new_ids = list(ids)
    manager._deduplicate()

    remaining = [uuid for uuid in ids if store.get_by_uuid(uuid) is not None]
    assert len(remaining) == 1

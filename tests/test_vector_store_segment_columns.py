from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_store_persists_segment_metadata(store: VectorStore) -> None:
    memory = MemoryObject(
        type="segment",
        content="Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1",
        ordinal=3,
        char_start=120,
        char_end=180,
    )
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["doc_id"] == "doc-1"
    assert record["metadata"]["ordinal"] == 3
    assert record["metadata"]["char_start"] == 120
    assert record["metadata"]["char_end"] == 180


def test_store_defaults_segment_metadata_to_none(store: VectorStore) -> None:
    memory = MemoryObject(type="doc", content="Plain content with no segment metadata.")
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["doc_id"] is None
    assert record["metadata"]["ordinal"] is None
    assert record["metadata"]["char_start"] is None
    assert record["metadata"]["char_end"] is None


def test_store_batch_persists_segment_metadata(store: VectorStore) -> None:
    memories = [
        MemoryObject(
            type="segment",
            content="Batch segment content long enough to embed.",
            doc_id="doc-2",
            ordinal=0,
            char_start=0,
            char_end=44,
        )
    ]
    ids = store.store_batch(memories)
    record = store.get_by_uuid(ids[0])
    assert record["metadata"]["doc_id"] == "doc-2"
    assert record["metadata"]["ordinal"] == 0
    assert record["metadata"]["char_start"] == 0
    assert record["metadata"]["char_end"] == 44

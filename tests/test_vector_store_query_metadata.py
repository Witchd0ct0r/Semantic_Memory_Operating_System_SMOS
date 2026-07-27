from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_query_result_metadata_matches_get_by_uuid_shape(store: VectorStore) -> None:
    memory = MemoryObject(
        type="segment",
        content="Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1",
        ordinal=3,
        char_start=120,
        char_end=180,
    )
    memory_id = store.store(memory)

    results = store.query("explicit consent processing", k=1)
    assert results[0]["id"] == memory_id
    meta = results[0]["metadata"]
    assert meta["doc_id"] == "doc-1"
    assert meta["ordinal"] == 3
    assert meta["char_start"] == 120
    assert meta["char_end"] == 180
    assert meta["source_ids"] == []
    assert meta["confidence"] == 1.0


def test_query_domain_result_metadata_matches_get_by_uuid_shape(store: VectorStore) -> None:
    memory = MemoryObject(
        type="segment",
        content="Requirement REQ-42 governs password rotation cadence.",
        tags=["security"],
        doc_id="doc-2",
        ordinal=0,
        char_start=0,
        char_end=54,
    )
    store.store(memory)

    results = store.query_domain("password rotation", k=1, domain_tags=["security"])
    assert results[0]["metadata"]["doc_id"] == "doc-2"
    assert results[0]["metadata"]["ordinal"] == 0


def test_get_segment_by_ordinal_returns_matching_segment(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="segment", content="First segment body text here.", doc_id="doc-3", ordinal=0, char_start=0, char_end=30),
        MemoryObject(type="segment", content="Second segment body text here.", doc_id="doc-3", ordinal=1, char_start=30, char_end=61),
    ]
    ids = store.store_batch(memories)

    record = store.get_segment_by_ordinal("doc-3", 1)
    assert record is not None
    assert record["id"] == ids[1]
    assert record["metadata"]["ordinal"] == 1


def test_get_segment_by_ordinal_returns_none_when_absent(store: VectorStore) -> None:
    assert store.get_segment_by_ordinal("doc-does-not-exist", 0) is None

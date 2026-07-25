from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_get_segment_ids_by_doc_returns_only_matching_doc(store: VectorStore) -> None:
    a = MemoryObject(type="segment", content="A", doc_id="doc-1", ordinal=0, char_start=0, char_end=1)
    b = MemoryObject(type="segment", content="B", doc_id="doc-1", ordinal=1, char_start=1, char_end=2)
    c = MemoryObject(type="segment", content="C", doc_id="doc-2", ordinal=0, char_start=0, char_end=1)
    ids = store.store_batch([a, b, c])

    result = store.get_segment_ids_by_doc("doc-1")
    assert sorted(result) == sorted(ids[:2])


def test_get_checkpoint_returns_none_when_absent(store: VectorStore) -> None:
    assert store.get_checkpoint("doc-x") is None


def test_save_and_get_checkpoint_round_trips(store: VectorStore) -> None:
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=5,
        next_leaf_index=2, segment_ids=["s1", "s2"], status="in_progress",
    )
    cp = store.get_checkpoint("doc-1")
    assert cp is not None
    assert cp["content_hash"] == "abc123"
    assert cp["total_leaves"] == 5
    assert cp["next_leaf_index"] == 2
    assert cp["segment_ids"] == ["s1", "s2"]
    assert cp["status"] == "in_progress"


def test_save_checkpoint_upserts_existing_row(store: VectorStore) -> None:
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=5,
        next_leaf_index=2, segment_ids=["s1", "s2"], status="in_progress",
    )
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=5,
        next_leaf_index=5, segment_ids=["s1", "s2", "s3", "s4", "s5"], status="complete",
    )
    cp = store.get_checkpoint("doc-1")
    assert cp["next_leaf_index"] == 5
    assert cp["status"] == "complete"
    assert len(cp["segment_ids"]) == 5


def test_delete_checkpoint_removes_row(store: VectorStore) -> None:
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=1,
        next_leaf_index=1, segment_ids=["s1"], status="complete",
    )
    store.delete_checkpoint("doc-1")
    assert store.get_checkpoint("doc-1") is None

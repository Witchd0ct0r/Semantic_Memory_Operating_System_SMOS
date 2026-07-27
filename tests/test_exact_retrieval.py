from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.exact_retrieval import expand_neighbors, recall_exact
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_recall_exact_returns_verbatim_segments_no_compression(store: VectorStore) -> None:
    store.store(MemoryObject(
        type="segment", content="Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1", ordinal=0, char_start=0, char_end=58,
    ))
    result = recall_exact("explicit consent processing", 3, store)
    assert result["mode"] == "extractive"
    assert result["confidence"] == 1.0
    assert result["segments"]
    assert result["segments"][0]["content"] == "Article 9(2)(b) permits processing with explicit consent."
    assert result["sources"] == [result["segments"][0]["id"]]


def test_recall_exact_no_matches_returns_uncertain(store: VectorStore) -> None:
    result = recall_exact("anything at all", 3, store)
    assert result == {"segments": [], "context": [], "sources": [], "confidence": 0.0, "mode": "uncertain"}


def test_recall_exact_includes_neighbor_context(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="segment", content="Neighbor before the matching clause.", doc_id="doc-2", ordinal=0, char_start=0, char_end=37),
        MemoryObject(type="segment", content="Explicit consent processing is required here.", doc_id="doc-2", ordinal=1, char_start=37, char_end=84),
        MemoryObject(type="segment", content="Neighbor after the matching clause.", doc_id="doc-2", ordinal=2, char_start=84, char_end=120),
    ]
    store.store_batch(memories)

    result = recall_exact("explicit consent processing", 1, store)
    context_contents = {c["content"] for c in result["context"]}
    assert "Neighbor before the matching clause." in context_contents
    assert "Neighbor after the matching clause." in context_contents


def test_expand_neighbors_skips_segments_without_doc_id(store: VectorStore) -> None:
    record = {"id": "x", "metadata": {"doc_id": None, "ordinal": None}}
    assert expand_neighbors(store, [record]) == []


def test_expand_neighbors_deduplicates_against_input_segments(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="segment", content="Segment zero body text here.", doc_id="doc-3", ordinal=0, char_start=0, char_end=29),
        MemoryObject(type="segment", content="Segment one body text here.", doc_id="doc-3", ordinal=1, char_start=29, char_end=57),
    ]
    ids = store.store_batch(memories)
    both = [store.get_by_uuid(i) for i in ids]

    neighbors = expand_neighbors(store, both)
    assert neighbors == []

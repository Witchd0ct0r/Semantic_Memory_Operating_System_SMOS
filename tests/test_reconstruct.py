from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.segmenter import ingest_document_text
from smos.memory.exact_retrieval import reconstruct
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_reconstruct_unknown_node_returns_error(store: VectorStore) -> None:
    result = reconstruct(store, "does-not-exist")
    assert result["text"] == ""
    assert result["segment_count"] == 0
    assert result["complete"] is False
    assert result["error"]


def test_reconstruct_memory_node_returns_error(store: VectorStore) -> None:
    store.graph.add_node("mem-1", "memory", "mem-1")
    result = reconstruct(store, "mem-1")
    assert result["complete"] is False
    assert result["error"]


def test_reconstruct_document_concatenates_leaves_in_ordinal_order(store: VectorStore) -> None:
    text = "# Title\nIntro paragraph text goes here.\n## Section A\n" + ("Sentence about topic A. " * 60)
    result = ingest_document_text("doc-1", text, store, MarkdownAdapter())

    reconstructed = reconstruct(store, "doc-1")
    assert reconstructed["complete"] is True
    assert reconstructed["text"] == text
    assert reconstructed["segment_count"] == result["segment_count"]


def test_reconstruct_section_returns_only_its_own_leaves(store: VectorStore) -> None:
    text = "# Title\n" + ("Document body paragraph. " * 70) + "\n## Section A\n" + ("Sentence about A topic. " * 40)
    result = ingest_document_text("doc-2", text, store, MarkdownAdapter())
    assert result["section_count"] >= 1

    children = store.graph.traverse("doc-2", max_hops=1, edge_types=["contains"])
    section_ids = [n["id"] for n in children if n["type"] == "section"]
    assert section_ids

    section_result = reconstruct(store, section_ids[0])
    assert 0 < section_result["segment_count"] < result["segment_count"]
    assert section_result["text"] in text

from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.segmenter import ingest_document_text
from smos.memory.vector_store import VectorStore


class _NoStructureAdapter:
    name = "no-structure"
    edge_types: frozenset[str] = frozenset()

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self):
        return None


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_small_document_links_directly_under_document_node(store: VectorStore) -> None:
    text = "A short document with no headings and no need to split further."
    result = ingest_document_text("doc-1", text, store, _NoStructureAdapter())

    assert result["segment_count"] == 1
    assert result["section_count"] == 0

    doc_node = store.graph.get_node("doc-1")
    assert doc_node is not None
    assert doc_node["type"] == "document"

    segment_id = result["segment_ids"][0]
    record = store.get_by_uuid(segment_id)
    assert record["content"] == text
    assert record["metadata"]["doc_id"] == "doc-1"
    assert record["metadata"]["ordinal"] == 0
    assert record["metadata"]["char_start"] == 0
    assert record["metadata"]["char_end"] == len(text)

    neighbors = store.graph.traverse("doc-1", max_hops=1, edge_types=["contains"])
    assert any(n["id"] == segment_id for n in neighbors)


def test_structured_document_creates_section_nodes_for_split_sections(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "Intro paragraph.\n"
        "## Section A\n" + ("Body sentence about topic A. " * 60) + "\n"
        "## Section B\n"
        "Short body B.\n"
    )
    result = ingest_document_text("doc-2", text, store, MarkdownAdapter())

    assert result["section_count"] >= 1
    assert result["segment_count"] == len(result["segment_ids"])

    doc_children = store.graph.traverse("doc-2", max_hops=1, edge_types=["contains"])
    section_nodes = [n for n in doc_children if n["type"] == "section"]
    assert len(section_nodes) == result["section_count"]

    for section in section_nodes:
        # traverse() is undirected, so this also returns the section's parent
        # document at hop 1 — filter it out to isolate the actual children.
        section_neighbors = store.graph.traverse(section["id"], max_hops=1, edge_types=["contains"])
        section_children = [n for n in section_neighbors if n["type"] != "document"]
        assert len(section_children) >= 2
        for child in section_children:
            assert child["type"] == "memory"


def test_segment_ordinals_are_sequential_and_reconstruct_original_text(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Section A\n" + ("Sentence about A. " * 60) + "\n"
        "## Section B\nShort B.\n"
    )
    result = ingest_document_text("doc-3", text, store, MarkdownAdapter())

    records = sorted(
        (store.get_by_uuid(sid) for sid in result["segment_ids"]),
        key=lambda r: r["metadata"]["ordinal"],
    )
    ordinals = [r["metadata"]["ordinal"] for r in records]
    assert ordinals == list(range(len(records)))

    reconstructed = "".join(
        text[r["metadata"]["char_start"]:r["metadata"]["char_end"]] for r in records
    )
    assert reconstructed == text

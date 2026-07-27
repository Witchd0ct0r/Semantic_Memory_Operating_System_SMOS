from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.exact_retrieval import reconstruct
from smos.memory.recall_engine import recall
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_two_stage_flow_exact_recall_then_reconstruct_document(store: VectorStore) -> None:
    md = (
        "# Title\nIntro paragraph about contract law basics.\n"
        "## Section A\n" + ("Provisions about liability under contract law. " * 40) + "\n"
        "## Section B\nA short closing clause about termination rights.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-e2e")
    ingest_result = do_ingest_document_schema(payload, store)
    assert ingest_result["status"] == "success"
    doc_id = ingest_result["doc_id"]

    stage2 = recall("termination rights", "exact", 3, "", "", "", "", store)
    assert stage2["mode"] == "extractive"
    assert stage2["segments"]
    assert any("termination" in s["content"].lower() for s in stage2["segments"])

    reconstructed = reconstruct(store, doc_id)
    assert reconstructed["complete"] is True
    assert reconstructed["text"] == md
    assert reconstructed["segment_count"] == ingest_result["segment_count"]


def test_reconstruct_section_returns_strict_subset_of_document(store: VectorStore) -> None:
    md = "# Title\nBody.\n## Section A\n" + ("Sentence about A topic. " * 60) + "\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-section")
    ingest_result = do_ingest_document_schema(payload, store)
    assert ingest_result["section_count"] >= 1

    children = store.graph.traverse(ingest_result["doc_id"], max_hops=1, edge_types=["contains"])
    section_ids = [n["id"] for n in children if n["type"] == "section"]
    assert section_ids

    section_result = reconstruct(store, section_ids[0])
    assert 0 < section_result["segment_count"] < ingest_result["segment_count"]
    assert section_result["text"] in md

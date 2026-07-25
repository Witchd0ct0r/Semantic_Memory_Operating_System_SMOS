from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import (
    ContentBlock,
    ContractBlockAdapter,
    ExternalHandle,
    IngestionPayload,
    _NoopAdapter,
    blocks_to_full_text,
    content_hash,
    derive_doc_id,
)
from smos.ingest.segmenter import _flatten_leaves, build_span_tree
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _markdown_payload(text: str, system: str, native_id: str) -> IngestionPayload:
    lines = text.splitlines(keepends=True)
    blocks: list[ContentBlock] = []
    buf = ""
    for line in lines:
        if line.startswith("#"):
            if buf:
                blocks.append(ContentBlock(block_type="paragraph", text=buf))
                buf = ""
            level = len(line) - len(line.lstrip("#"))
            blocks.append(ContentBlock(block_type="heading", text=line, heading_level=level))
        else:
            buf += line
    if buf:
        blocks.append(ContentBlock(block_type="paragraph", text=buf))
    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id), blocks=blocks
    )


def test_fresh_ingest_stores_segments_and_wires_containment(store: VectorStore) -> None:
    text = (
        "# Title\nIntro paragraph text goes here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-1")

    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    assert result["resumed"] is False
    assert result["segment_count"] == len(result["segment_ids"])
    assert result["section_count"] >= 1

    doc_node = store.graph.get_node(result["doc_id"])
    assert doc_node is not None
    assert doc_node["type"] == "document"


def test_invalid_payload_is_quarantined_not_raised(store: VectorStore) -> None:
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="bad-doc"), blocks=[]
    )
    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "quarantined"
    assert result["quarantine_key"]
    assert any("at least one block" in e for e in result["errors"])
    row = store.get_quarantine(result["quarantine_key"])
    assert row is not None


def test_resync_replaces_segments_for_same_doc_id(store: VectorStore) -> None:
    payload_v1 = _markdown_payload("# T\nOriginal body text here.\n", "wiki", "doc-sync")
    result1 = do_ingest_document_schema(payload_v1, store)
    assert result1["status"] == "success"

    payload_v2 = _markdown_payload(
        "# T\nUpdated body text, now quite different indeed.\n", "wiki", "doc-sync"
    )
    result2 = do_ingest_document_schema(payload_v2, store)
    assert result2["status"] == "success"
    assert result2["doc_id"] == result1["doc_id"]

    for old_id in result1["segment_ids"]:
        assert store.get_by_uuid(old_id) is None
    for new_id in result2["segment_ids"]:
        assert store.get_by_uuid(new_id) is not None


def test_resume_continues_from_checkpoint_without_restoring_earlier_leaves(
    store: VectorStore,
) -> None:
    text = (
        "# Title\nIntro paragraph text goes here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-resume")
    doc_id = derive_doc_id("wiki", "doc-resume")
    full_text, block_spans = blocks_to_full_text(payload.blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, block_spans)
    root = build_span_tree(full_text, adapter)
    leaves = _flatten_leaves(root)
    assert len(leaves) >= 3

    partial_count = 2
    partial_memories = [
        MemoryObject(
            type="segment", content=full_text[leaf.start:leaf.end],
            doc_id=doc_id, ordinal=i, char_start=leaf.start, char_end=leaf.end,
        )
        for i, leaf in enumerate(leaves[:partial_count])
    ]
    partial_ids = store.store_batch(partial_memories)
    store.graph.add_node(doc_id, "document", doc_id)
    store.save_checkpoint(
        doc_id=doc_id, content_hash=content_hash(full_text), total_leaves=len(leaves),
        next_leaf_index=partial_count, segment_ids=partial_ids, status="in_progress",
    )

    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    assert result["resumed"] is True
    assert result["segment_count"] == len(leaves)
    assert result["segment_ids"][:partial_count] == partial_ids


def test_containment_not_wired_until_ingest_completes(store: VectorStore) -> None:
    text = "# Title\nBody.\n## Section A\n" + ("More text here. " * 60)
    payload = _markdown_payload(text, "wiki", "doc-partial")
    doc_id = derive_doc_id("wiki", "doc-partial")
    full_text, block_spans = blocks_to_full_text(payload.blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, block_spans)
    root = build_span_tree(full_text, adapter)
    leaves = _flatten_leaves(root)

    store.graph.add_node(doc_id, "document", doc_id)
    store.save_checkpoint(
        doc_id=doc_id, content_hash=content_hash(full_text), total_leaves=len(leaves),
        next_leaf_index=0, segment_ids=[], status="in_progress",
    )

    neighbors = store.graph.traverse(doc_id, max_hops=1, edge_types=["contains"])
    assert neighbors == []

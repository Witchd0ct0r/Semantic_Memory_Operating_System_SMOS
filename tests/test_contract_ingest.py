from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
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


def test_multi_chunk_ingest_exceeds_checkpoint_chunk_size(store: VectorStore) -> None:
    # No sentence-terminal punctuation, so the segmenter's semantic layer sees
    # one giant "sentence" and falls straight through to fixed-size 700-char
    # windowing — deterministically ceil(50000/700) = 72 leaves, comfortably
    # over _CHECKPOINT_CHUNK_SIZE (64), exercising the chunk loop's second
    # iteration and its cross-chunk ordinal/index bookkeeping.
    text = ("word " * 10000).strip()
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-multichunk"),
        blocks=[ContentBlock(block_type="paragraph", text=text)],
    )

    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    assert len(result["segment_ids"]) > 64
    checkpoint = store.get_checkpoint(result["doc_id"])
    assert checkpoint is not None
    assert checkpoint["status"] == "complete"
    assert checkpoint["next_leaf_index"] == len(result["segment_ids"])


def test_resume_reconciles_orphan_segments_from_crash_window(store: VectorStore) -> None:
    # Simulates the crash window between store.store_batch() committing a
    # chunk's segments and store.save_checkpoint() recording that chunk: the
    # segments physically exist under doc_id, but the checkpoint's
    # segment_ids list doesn't include one of them (the "orphan"). On resume,
    # do_ingest_document_schema must reconcile — delete the orphan and
    # re-store fresh, deduplicated segments — rather than leaving the orphan
    # alongside a freshly stored duplicate.
    text = (
        "# Title\nIntro paragraph text goes here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-orphan")
    doc_id = derive_doc_id("wiki", "doc-orphan")
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
    # Checkpoint only records the first id — the second is an "orphan" that
    # was persisted (store_batch succeeded) but never made it into a
    # checkpoint write (the process "crashed" before save_checkpoint ran).
    orphan_id = partial_ids[1]
    store.save_checkpoint(
        doc_id=doc_id, content_hash=content_hash(full_text), total_leaves=len(leaves),
        next_leaf_index=1, segment_ids=[partial_ids[0]], status="in_progress",
    )

    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    assert result["resumed"] is True
    assert result["segment_count"] == len(leaves)
    assert len(result["segment_ids"]) == len(set(result["segment_ids"]))
    assert orphan_id not in result["segment_ids"]
    assert store.get_by_uuid(orphan_id) is None


def test_schema_ingest_wires_reference_edges_when_adapter_supports_it(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten billion dollars.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be submitted by March 31.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-schema-refs")

    result = do_ingest_document_schema(payload, store, adapter=MarkdownAdapter())

    assert result["status"] == "success"
    assert result["reference_count"] >= 1
    assert store.graph.get_node("concept:definitions") is not None


def test_schema_ingest_reference_count_is_zero_without_adapter(store: VectorStore) -> None:
    payload = _markdown_payload("# T\nBody text with no references.\n", "wiki", "doc-no-adapter")
    result = do_ingest_document_schema(payload, store)
    assert result["reference_count"] == 0


def test_domain_tags_persist_on_document_node_metadata(store: VectorStore) -> None:
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-domains"),
        blocks=[ContentBlock(
            block_type="paragraph",
            text="Body text about lead and cadmium exposure limits for covered products.",
        )],
        domain_tags=["legal", "environmental"],
    )
    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    doc_node = store.graph.get_node(result["doc_id"])
    assert doc_node["metadata"]["domain_tags"] == ["legal", "environmental"]


def test_domain_tags_persist_on_segments_and_scope_query_domain(store: VectorStore) -> None:
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-domains-2"),
        blocks=[ContentBlock(
            block_type="paragraph",
            text="Body text about lead and cadmium exposure limits for covered products.",
        )],
        domain_tags=["environmental"],
    )
    result = do_ingest_document_schema(payload, store)

    scoped = store.query_domain("lead cadmium exposure limits", k=5, domain_tags=["environmental"])
    assert any(r["id"] in result["segment_ids"] for r in scoped)

    unscoped = store.query_domain("lead cadmium exposure limits", k=5, domain_tags=["unrelated-domain"])
    assert not any(r["id"] in result["segment_ids"] for r in unscoped)

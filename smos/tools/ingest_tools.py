"""
Schema-driven document ingestion for SMOS.

Public callables:
    do_ingest_document_schema()
    delete_document_segments()
"""
from __future__ import annotations

from typing import Optional

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.ingest.adapters.base import DomainAdapter
from smos.ingest.contract import (
    ContractBlockAdapter,
    IngestionPayload,
    _NoopAdapter,
    blocks_to_full_text,
    content_hash,
    derive_doc_id,
    quarantine_payload,
    validate_payload,
)
from smos.ingest.link_wiring import wire_source_links
from smos.ingest.segmenter import _flatten_leaves, _wire_containment, _wire_references, build_span_tree

# ─── Schema-driven ingestion (Pillar G) ───────────────────────────────────────

_CHECKPOINT_CHUNK_SIZE = 64


def delete_document_segments(store: VectorStore, doc_id: str) -> None:
    """Remove all segments and section nodes for `doc_id`, leaving the
    `document` graph node itself intact (re-sync repopulates underneath it,
    it doesn't need to be recreated)."""
    segment_ids = store.get_segment_ids_by_doc(doc_id)
    if segment_ids:
        store.delete_batch(segment_ids)
    sections = [
        n for n in store.graph.traverse(doc_id, max_hops=1, edge_types=["contains"])
        if n["type"] == "section"
    ]
    for section in sections:
        store.graph.remove_node(section["id"])
        store.delete_summary(section["id"])
    store.delete_blocks(doc_id)


def do_ingest_document_schema(
    payload: IngestionPayload,
    store: VectorStore,
    adapter: Optional[DomainAdapter] = None,
) -> dict:
    """Validate, segment, and store a structure-preserving ingestion payload.
    Ingests of the same document id are serialized by a per-document lock.
    See `_ingest_locked` for the full behavior."""
    doc_id = derive_doc_id(payload.external_handle.system, payload.external_handle.native_id)
    with store.document_lock(doc_id):
        return _ingest_locked(payload, store, adapter)


def _is_unchanged_resend(
    store: VectorStore, doc_id: str, payload: IngestionPayload, checkpoint: Optional[dict], text_hash: str
) -> bool:
    """True when this payload would rebuild exactly the segments already stored: the previous ingest is
    complete, the text is identical, and tags and doc_type are unchanged. Anything else replaces them."""
    if checkpoint is None or checkpoint["status"] != "complete" or checkpoint["content_hash"] != text_hash:
        return False
    node = store.graph.get_node(doc_id)
    if node is None or node["type"] != "document":
        return False
    meta = node["metadata"]
    return (
        not meta.get("placeholder")
        and list(meta.get("domain_tags") or []) == list(payload.domain_tags)
        and meta.get("doc_type") == payload.doc_type
        and bool(store.get_segment_ids_by_doc(doc_id))
    )


def _refresh_unchanged(
    store: VectorStore, payload: IngestionPayload, doc_id: str, block_spans: list[tuple[int, int]]
) -> dict:
    """Re-send of an unchanged document: keep the segments (and so their ids, summaries and links) and refresh
    only what the payload carries besides the text: the stored blocks, the document node, source links, summary."""
    store.save_blocks(
        doc_id, [(start, end, block.block_type) for (start, end), block in zip(block_spans, payload.blocks)]
    )
    store.graph.upsert_node(doc_id, "document", payload.doc_name or doc_id, metadata=_document_metadata(payload))
    link_count = wire_source_links(store.graph, payload, doc_id, store.get_document_hashes)
    if payload.summary:
        store.set_summary(doc_id, payload.summary, payload.summary_source or "")
    else:
        store.delete_summary(doc_id)
    segment_ids = store.get_segment_ids_by_doc(doc_id)
    return {
        "status": "success",
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": None,
        "reference_count": None,
        "link_count": link_count,
        "resumed": False,
        "segments_kept": True,
    }


def _document_metadata(payload: IngestionPayload) -> dict:
    return {
        "domain_tags": payload.domain_tags,
        "doc_type": payload.doc_type,
        "system": payload.external_handle.system,
        "native_id": payload.external_handle.native_id,
        "metadata": payload.metadata,
    }


def _ingest_locked(
    payload: IngestionPayload,
    store: VectorStore,
    adapter: Optional[DomainAdapter] = None,
) -> dict:
    """Validate, segment, and store a structure-preserving ingestion payload.

    Deterministic-id upsert: re-ingesting the same external_handle
    (system+native_id) resolves to the same doc_id. A prior complete ingest
    is replaced; a prior in-progress checkpoint with matching content is
    resumed from where it left off; a prior in-progress checkpoint with
    different content (client sent an update mid-flight) is discarded and
    restarted.
    """
    errors = validate_payload(payload)
    if errors:
        key = quarantine_payload(store, payload, errors)
        return {"status": "quarantined", "quarantine_key": key, "errors": errors}

    doc_id = derive_doc_id(payload.external_handle.system, payload.external_handle.native_id)
    full_text, block_spans = blocks_to_full_text(payload.blocks)
    text_hash = content_hash(full_text)

    checkpoint = store.get_checkpoint(doc_id)
    resumed = False
    if _is_unchanged_resend(store, doc_id, payload, checkpoint, text_hash):
        return _refresh_unchanged(store, payload, doc_id, block_spans)
    if checkpoint is not None:
        if checkpoint["status"] == "complete" or checkpoint["content_hash"] != text_hash:
            delete_document_segments(store, doc_id)
            store.delete_checkpoint(doc_id)
            checkpoint = None
        else:
            resumed = True

    base_adapter = adapter or _NoopAdapter()
    block_adapter = ContractBlockAdapter(base_adapter, full_text, block_spans)
    root = build_span_tree(full_text, block_adapter)
    leaves = _flatten_leaves(root)

    if checkpoint is not None and checkpoint["total_leaves"] != len(leaves):
        # Segmentation of identical content produced a different leaf count than
        # the checkpointed run (should not happen since content_hash matched, but
        # guards against non-determinism rather than silently misaligning leaves
        # to the wrong stored segment ids).
        delete_document_segments(store, doc_id)
        store.delete_checkpoint(doc_id)
        checkpoint = None
        resumed = False

    if resumed:
        # A crash between store_batch() committing a chunk's segments and
        # save_checkpoint() recording that chunk can leave segments stored
        # under this doc_id that the checkpoint doesn't know about yet.
        # Reconcile before continuing so they aren't duplicated below.
        actual_ids = set(store.get_segment_ids_by_doc(doc_id))
        checkpoint_ids = set(checkpoint["segment_ids"])
        orphans = list(actual_ids - checkpoint_ids)
        if orphans:
            store.delete_batch(orphans)

    store.save_blocks(
        doc_id, [(start, end, block.block_type) for (start, end), block in zip(block_spans, payload.blocks)]
    )
    store.graph.upsert_node(doc_id, "document", payload.doc_name or doc_id, metadata=_document_metadata(payload))
    segment_tags = list(payload.domain_tags)
    if payload.doc_type:
        segment_tags.append(f"doctype:{payload.doc_type}")

    start_index = checkpoint["next_leaf_index"] if checkpoint else 0
    segment_ids: list[str] = list(checkpoint["segment_ids"]) if checkpoint else []

    for leaf, sid in zip(leaves[:start_index], segment_ids):
        leaf.memory_id = sid

    remaining = leaves[start_index:]
    for offset in range(0, len(remaining), _CHECKPOINT_CHUNK_SIZE):
        chunk = remaining[offset : offset + _CHECKPOINT_CHUNK_SIZE]
        chunk_index_start = start_index + offset
        memories = [
            MemoryObject(
                type="segment",
                content=full_text[leaf.start : leaf.end],
                doc_id=doc_id,
                ordinal=chunk_index_start + i,
                char_start=leaf.start,
                char_end=leaf.end,
                tags=list(segment_tags),
            )
            for i, leaf in enumerate(chunk)
        ]
        chunk_ids = store.store_batch(memories)
        for leaf, sid in zip(chunk, chunk_ids):
            leaf.memory_id = sid
        segment_ids.extend(chunk_ids)
        store.save_checkpoint(
            doc_id=doc_id,
            content_hash=text_hash,
            total_leaves=len(leaves),
            next_leaf_index=chunk_index_start + len(chunk),
            segment_ids=segment_ids,
            status="in_progress",
        )

    section_count = _wire_containment(store.graph, doc_id, root)
    reference_count = _wire_references(store.graph, block_adapter, full_text, leaves, doc_id=doc_id)
    link_count = wire_source_links(store.graph, payload, doc_id, store.get_document_hashes)
    store.save_checkpoint(
        doc_id=doc_id,
        content_hash=text_hash,
        total_leaves=len(leaves),
        next_leaf_index=len(leaves),
        segment_ids=segment_ids,
        status="complete",
    )

    if payload.summary:
        store.set_summary(doc_id, payload.summary, payload.summary_source or "")
    else:
        store.delete_summary(doc_id)

    return {
        "status": "success",
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
        "reference_count": reference_count,
        "link_count": link_count,
        "resumed": resumed,
        "segments_kept": False,
    }

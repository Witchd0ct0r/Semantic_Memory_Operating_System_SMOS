from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

TEXT = ["1 Scope\nThis standard applies to widgets used in vehicles of all kinds.\n",
        "2 Limits\nThe maximum permitted deflection shall not exceed two millimetres.\n"]


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _payload(texts: list[str] = TEXT, tags: list[str] | None = None, summary: str | None = None, **extra) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="s", native_id="D1"),
        blocks=[ContentBlock(block_type="paragraph", text=t) for t in texts],
        domain_tags=tags or ["norm:A"], summary=summary, **extra,
    )


def test_identical_resend_keeps_segment_ids_and_writes_blocks(store: VectorStore) -> None:
    first = do_ingest_document_schema(_payload(), store)
    assert first["segments_kept"] is False
    doc_id = first["doc_id"]
    store.delete_blocks(doc_id)                                    # a document ingested before blocks were stored
    again = do_ingest_document_schema(_payload(), store)
    assert again["status"] == "success" and again["segments_kept"] is True and again["resumed"] is False
    assert again["segment_ids"] == first["segment_ids"] and again["segment_count"] == first["segment_count"]
    assert again["doc_id"] == doc_id and again["section_count"] is None and again["reference_count"] is None
    assert [b["ordinal"] for b in store.get_blocks(doc_id)] == [0, 1]
    assert store.get_segment_ids_by_doc(doc_id) == first["segment_ids"]


def test_changed_text_replaces_segments(store: VectorStore) -> None:
    first = do_ingest_document_schema(_payload(), store)
    again = do_ingest_document_schema(_payload([TEXT[0], "2 Limits\nA different limit sentence entirely.\n"]), store)
    assert again["segments_kept"] is False
    assert set(again["segment_ids"]).isdisjoint(first["segment_ids"])


def test_changed_tags_replaces_segments_and_memory_tags(store: VectorStore) -> None:
    first = do_ingest_document_schema(_payload(tags=["norm:A"]), store)
    again = do_ingest_document_schema(_payload(tags=["norm:B"]), store)
    assert again["segments_kept"] is False and set(again["segment_ids"]).isdisjoint(first["segment_ids"])
    assert store.unknown_tags(["norm:A"]) == ["norm:A"] and store.unknown_tags(["norm:B"]) == []


def test_resend_without_summary_deletes_document_summary(store: VectorStore) -> None:
    doc_id = do_ingest_document_schema(_payload(summary="About widgets.", summary_source="parser-1"), store)["doc_id"]
    assert store.get_summaries([doc_id])[doc_id]["summary"] == "About widgets."
    do_ingest_document_schema(_payload(summary="Updated summary."), store)
    assert store.get_summaries([doc_id])[doc_id]["summary"] == "Updated summary."
    do_ingest_document_schema(_payload(), store)                   # documented gotcha: no summary sent, none kept
    assert store.get_summaries([doc_id]) == {}


def test_segment_summary_survives_fast_path(store: VectorStore) -> None:
    first = do_ingest_document_schema(_payload(), store)
    seg = first["segment_ids"][0]
    store.set_summary(seg, "Segment note written by a parser.", "parser-1")
    again = do_ingest_document_schema(_payload(), store)
    assert again["segments_kept"] is True
    assert store.get_summaries([seg])[seg]["summary"] == "Segment note written by a parser."

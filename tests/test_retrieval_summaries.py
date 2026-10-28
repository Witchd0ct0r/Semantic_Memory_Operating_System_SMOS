from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.semantic_tools import semantic_query


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow())


def test_query_returns_stored_summary(store: VectorStore) -> None:
    memory_id = store.store(_mem("PostgreSQL uses MVCC for transaction isolation."))
    store.set_summary(memory_id, "Postgres isolates transactions via MVCC.", "parser-1")
    result = semantic_query("how does postgres isolate transactions", 3, store)
    item = next(r for r in result["results"] if r["id"] == memory_id)
    assert item["content"] == "PostgreSQL uses MVCC for transaction isolation."
    assert item["summary"] == "Postgres isolates transactions via MVCC."
    assert item["summary_source"] == "parser-1"
    assert result["mode"] == "extractive"


def test_query_summary_null_when_absent(store: VectorStore) -> None:
    memory_id = store.store(_mem("Redis is an in-memory data store used for caching."))
    result = semantic_query("in-memory caching store", 3, store)
    item = next(r for r in result["results"] if r["id"] == memory_id)
    assert item["summary"] is None
    assert item["summary_source"] is None


def test_query_on_empty_store_is_uncertain(store: VectorStore) -> None:
    result = semantic_query("anything at all here", 3, store)
    assert result["results"] == []
    assert result["sources"] == []
    assert result["mode"] == "uncertain"
    assert result["confidence"] == 0.0


def test_segment_hit_carries_its_documents_summary(store: VectorStore) -> None:
    from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload
    from smos.tools.ingest_tools import do_ingest_document_schema

    payload = IngestionPayload(
        external_handle=ExternalHandle(system="sys", native_id="R-1"),
        blocks=[ContentBlock(block_type="paragraph", text="The system shall limit radiated emissions.")],
        summary="Caps radiated emissions.",
        summary_source="parser-9",
    )
    doc_id = do_ingest_document_schema(payload, store)["doc_id"]
    result = semantic_query("limit radiated emissions", 3, store)
    item = next(r for r in result["results"] if r["metadata"].get("doc_id") == doc_id)
    assert item["summary"] is None  # the segment itself has none
    assert item["document_summary"] == "Caps radiated emissions."
    assert item["document_summary_source"] == "parser-9"


def test_plain_memory_has_no_document_summary(store: VectorStore) -> None:
    store.store(_mem("Redis is an in-memory data store used for caching."))
    result = semantic_query("in-memory caching store", 3, store)
    assert result["results"][0]["document_summary"] is None


def _sectioned_payload(native_id: str, body_b: str) -> "IngestionPayload":
    from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload

    return IngestionPayload(
        external_handle=ExternalHandle(system="sys", native_id=native_id),
        blocks=[
            ContentBlock(block_type="heading", text="# Title\n", heading_level=1),
            ContentBlock(block_type="paragraph", text="Intro paragraph text goes here.\n"),
            ContentBlock(block_type="heading", text="## Emissions\n", heading_level=2),
            ContentBlock(block_type="paragraph",
                         text=body_b + ("The device shall limit radiated emissions. " * 60) + "\n"),
        ],
    )


def _first_section_id(store: VectorStore, doc_id: str) -> str:
    sections = [n for n in store.graph.traverse(doc_id, max_hops=2, edge_types=["contains"])
                if n["type"] == "section"]
    assert sections
    return sections[-1]["id"]


def test_segment_hit_carries_its_sections_summary(store: VectorStore) -> None:
    from smos.tools.ingest_tools import do_ingest_document_schema

    doc_id = do_ingest_document_schema(
        _sectioned_payload("S-1", "The device shall limit radiated emissions below the limit.\n"), store
    )["doc_id"]
    section_id = _first_section_id(store, doc_id)
    store.set_summary(section_id, "Emission limits section.", "parser-3")
    result = semantic_query("device shall limit radiated emissions", 5, store)
    hit = next(r for r in result["results"]
               if r["metadata"].get("doc_id") == doc_id and "radiated" in r["content"])
    assert hit["section_summary"] == "Emission limits section."
    assert hit["section_summary_source"] == "parser-3"


def test_reingest_drops_summaries_of_removed_sections(store: VectorStore) -> None:
    from smos.tools.ingest_tools import do_ingest_document_schema

    doc_id = do_ingest_document_schema(_sectioned_payload("S-2", "Old body text for the section.\n"), store)["doc_id"]
    section_id = _first_section_id(store, doc_id)
    store.set_summary(section_id, "stale section summary", "p")
    do_ingest_document_schema(_sectioned_payload("S-2", "Completely different new body text.\n"), store)
    assert store.get_summaries([section_id]) == {}

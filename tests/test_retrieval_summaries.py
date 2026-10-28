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

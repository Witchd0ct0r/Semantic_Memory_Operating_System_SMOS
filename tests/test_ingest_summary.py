from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, derive_doc_id
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _payload(**extra) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="sys", native_id="N-1"),
        blocks=[ContentBlock(block_type="paragraph", text="The device shall limit conducted emissions.")],
        **extra,
    )


def test_payload_summary_persisted_on_document_node(store: VectorStore) -> None:
    result = do_ingest_document_schema(_payload(summary="Limits conducted emissions.", summary_source="p1"), store)
    assert result["status"] == "success"
    got = store.get_summaries([result["doc_id"]])[result["doc_id"]]
    assert got == {"summary": "Limits conducted emissions.", "source": "p1"}


def test_resync_without_summary_clears_old_summary(store: VectorStore) -> None:
    first = do_ingest_document_schema(_payload(summary="old summary"), store)
    do_ingest_document_schema(_payload(), store)
    assert store.get_summaries([first["doc_id"]]) == {}


def test_no_summary_means_no_row(store: VectorStore) -> None:
    result = do_ingest_document_schema(_payload(), store)
    assert store.get_summaries([result["doc_id"]]) == {}
    assert result["doc_id"] == derive_doc_id("sys", "N-1")

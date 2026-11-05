from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, derive_doc_id
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _send(store, *, version=None, deep_link=None, text="The unit shall start."):
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="cb", native_id="R1", version=version, deep_link=deep_link),
        doc_name="R1",
        blocks=[ContentBlock(block_type="paragraph", text=text)],
    )
    assert do_ingest_document_schema(payload, store)["status"] == "success"


def test_version_and_deep_link_are_stored_and_listed(store):
    _send(store, version="7", deep_link="https://tracker.example/item/1")
    total, docs = store.list_documents()
    assert total == 1
    assert docs[0]["external_handle"] == {
        "system": "cb", "native_id": "R1", "version": "7", "deep_link": "https://tracker.example/item/1",
    }
    meta = store.graph.get_node(derive_doc_id("cb", "R1"))["metadata"]
    assert meta["version"] == "7" and meta["deep_link"] == "https://tracker.example/item/1"


def test_unchanged_resend_refreshes_a_new_version(store):
    _send(store, version="7")
    _send(store, version="8")
    assert store.list_documents()[1][0]["external_handle"]["version"] == "8"


def test_absent_fields_are_null(store):
    _send(store)
    handle = store.list_documents()[1][0]["external_handle"]
    assert handle["version"] is None and handle["deep_link"] is None


def test_hits_expose_rank_and_fused_score_in_ranking_order(store):
    from smos.compression.context_builder import build_context

    for i, text in enumerate([
        "The brake controller shall limit deceleration jerk.",
        "The brake actuator shall hold pressure for two seconds.",
        "Wiper intervals shall be adjustable by the driver.",
    ]):
        payload = IngestionPayload(
            external_handle=ExternalHandle(system="cb", native_id=f"B{i}"),
            blocks=[ContentBlock(block_type="paragraph", text=text)],
        )
        assert do_ingest_document_schema(payload, store)["status"] == "success"
    page1 = build_context("brake controller deceleration", 2, store).results
    page2 = build_context("brake controller deceleration", 2, store, offset=2).results
    ranked = [r for r in page1 if r.rank is not None]
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))
    scores = [r.rank_score for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(r.rank >= 3 for r in page2 if r.rank is not None)

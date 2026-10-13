from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    derive_doc_id,
)
from smos.memory.requirement_context import requirement_context
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

SYSTEM = "cb-e2e"


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_adapter(RequirementsAdapter())
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _ingest(store, native_id, text, *, links=(), tags=(), doc_type="requirement", name=None):
    payload = IngestionPayload(
        external_handle=ExternalHandle(system=SYSTEM, native_id=native_id),
        doc_name=name or native_id,
        blocks=[ContentBlock(block_type="paragraph", text=text)],
        doc_type=doc_type,
        domain_tags=list(tags),
        source_links=[SourceLink(target_native_id=t, edge_type=e, metadata=m) for (t, e, m) in links],
    )
    assert do_ingest_document_schema(payload, store)["status"] == "success"


@pytest.fixture
def corpus(store: VectorStore) -> None:
    # normative sources
    _ingest(
        store, "STD-26262-6",
        "9.4.2 The software unit design shall avoid single points of failure in the "
        "brake actuation path.",
        doc_type="standard", tags=["iso26262"], name="ISO 26262-6",
    )
    _ingest(
        store, "REG-71",
        "Article 7. Manufacturers shall ensure that motor vehicle braking systems "
        "meet the prescribed stopping distances.",
        doc_type="legal", tags=["eu-law"], name="Braking Regulation",
    )
    # three-level chain
    _ingest(
        store, "SYS1-10",
        "Occupants must be protected from injury during an abrupt stop.",
        tags=["level:SYS.1", "domain:safety"], name="Occupant protection",
    )
    _ingest(
        store, "SYS2-20",
        "The brake controller shall apply full deceleration within 150 ms of a "
        "collision signal.",
        tags=["level:SYS.2", "domain:safety"], name="Collision braking",
        links=[
            ("SYS1-10", "depends_on", {}),
            ("STD-26262-6", "cites", {"clause": "9.4.2"}),
        ],
    )
    _ingest(
        store, "SYS3-30",
        "The brake actuator driver shall energise the hydraulic valve when the "
        "collision flag is set.",
        tags=["level:SYS.3", "domain:safety"], name="Valve driver",
        links=[("SYS2-20", "depends_on", {})],
    )
    # near duplicate of SYS2-20 and an unrelated item
    _ingest(
        store, "SYS2-21",
        "The brake controller shall apply full deceleration within 200 ms of a "
        "collision signal.",
        tags=["level:SYS.2", "domain:safety"], name="Collision braking (copy)",
    )
    _ingest(
        store, "SYS2-99",
        "The infotainment unit shall display the album artwork.",
        tags=["level:SYS.2", "domain:comfort"], name="Artwork",
    )


def test_stored_item_gets_all_four_sections(store, corpus):
    result = requirement_context(store, system=SYSTEM, native_id="SYS2-20")

    linked = {i["doc_id"]: i for i in result["linked"]}
    assert linked[derive_doc_id(SYSTEM, "SYS1-10")]["reason"] == "upstream (depends_on)"
    assert linked[derive_doc_id(SYSTEM, "SYS3-30")]["reason"] == "downstream (depends_on)"

    peers = {i["doc_id"]: i for i in result["peers"]}
    assert peers[derive_doc_id(SYSTEM, "SYS2-21")]["near_duplicate"] is True
    assert derive_doc_id(SYSTEM, "SYS2-99") not in peers  # different domain

    assert result["normative"][0]["doc_id"] == derive_doc_id(SYSTEM, "STD-26262-6")
    assert result["normative"][0]["reason"] == "explicit citation"
    assert result["normative"][0]["clause"] == "9.4.2"
    assert result["coverage"]["normative_by_source"]["standard"] >= 1
    assert result["coverage"]["has_upstream"] and result["coverage"]["has_downstream"]
    assert result["coverage"]["near_duplicates"] == 1


def test_linked_parent_is_returned_because_of_the_link_not_the_ranking(store, corpus):
    # A draft whose wording shares nothing with its SYS.1 parent still gets the
    # parent through its declared link; retrieval rank plays no part.
    result = requirement_context(
        store,
        text="Pedal travel beyond the end stop shall be mechanically prevented.",
        system=SYSTEM,
        declared_links=[{"target_native_id": "SYS1-10", "edge_type": "depends_on"}],
        domain_tags=["domain:safety"],
        level="SYS.2",
    )
    linked = {i["doc_id"]: i for i in result["linked"]}
    parent = linked[derive_doc_id(SYSTEM, "SYS1-10")]
    assert parent["reason"] == "declared depends_on"
    assert parent["hops"] == 1
    # the parent's other downstream item is reachable at hop 2
    assert linked[derive_doc_id(SYSTEM, "SYS2-20")]["hops"] == 2


def test_draft_without_links_reports_missing_traceability(store, corpus):
    result = requirement_context(
        store,
        text="The brake controller shall apply full deceleration within 180 ms of a collision signal.",
        domain_tags=["domain:safety"],
        level="SYS.2",
    )
    assert result["linked"] == []
    assert result["coverage"]["has_upstream"] is False
    assert result["coverage"]["near_duplicates"] >= 1
    assert any(i["doc_id"] == derive_doc_id(SYSTEM, "SYS2-20") for i in result["peers"])


def test_resync_with_removed_link_changes_the_pack(store, corpus):
    _ingest(
        store, "SYS2-20",
        "The brake controller shall apply full deceleration within 150 ms of a "
        "collision signal.",
        tags=["level:SYS.2", "domain:safety"], name="Collision braking",
        links=[("STD-26262-6", "cites", {"clause": "9.4.2"})],
    )
    result = requirement_context(store, system=SYSTEM, native_id="SYS2-20")
    linked_ids = {i["doc_id"] for i in result["linked"]}
    assert derive_doc_id(SYSTEM, "SYS1-10") not in linked_ids
    assert derive_doc_id(SYSTEM, "SYS3-30") in linked_ids  # incoming link survives
    assert result["coverage"]["has_upstream"] is False

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
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_adapter(RequirementsAdapter())
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _item(native_id, text, *, system="cb-test", name=None, links=(), tags=(),
          doc_type="requirement", metadata=None) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=name or native_id,
        blocks=[ContentBlock(block_type="paragraph", text=text)],
        doc_type=doc_type,
        domain_tags=list(tags),
        metadata=metadata or {},
        source_links=[
            SourceLink(target_native_id=t, edge_type=e, metadata=m) for (t, e, m) in links
        ],
    )


def _id(native_id: str, system: str = "cb-test") -> str:
    return derive_doc_id(system, native_id)


def _out(store: VectorStore, native_id: str, system: str = "cb-test") -> set[tuple[str, str]]:
    return {
        (e["target_id"], e["edge_type"])
        for e in store.graph.get_edges(_id(native_id, system), "out")
        if e["edge_type"] != "contains"
    }


def test_depends_on_link_creates_edge_in_the_direction_sent(store):
    result = do_ingest_document_schema(
        _item("SYS2-1", "The unit shall brake.", links=[("SYS1-1", "depends_on", {})]), store
    )
    assert result["status"] == "success"
    assert result["link_count"] == 1
    assert _out(store, "SYS2-1") == {(_id("SYS1-1"), "depends_on")}


def test_missing_target_becomes_placeholder_then_is_upgraded_by_real_ingest(store):
    do_ingest_document_schema(
        _item("SYS2-1", "The unit shall brake.", links=[("SYS1-1", "depends_on", {})]), store
    )
    placeholder = store.graph.get_node(_id("SYS1-1"))
    assert placeholder["type"] == "document"
    assert placeholder["metadata"]["placeholder"] is True
    assert placeholder["name"] == "SYS1-1"

    do_ingest_document_schema(
        _item("SYS1-1", "Occupants shall be protected.", name="Occupant protection"), store
    )
    upgraded = store.graph.get_node(_id("SYS1-1"))
    assert upgraded["name"] == "Occupant protection"
    assert "placeholder" not in upgraded["metadata"]
    assert upgraded["metadata"]["doc_type"] == "requirement"
    assert _out(store, "SYS2-1") == {(_id("SYS1-1"), "depends_on")}


def test_target_system_in_link_metadata_selects_the_target_namespace(store):
    do_ingest_document_schema(
        _item("R-1", "The unit shall log.", links=[("ISO-9", "cites", {"target_system": "std", "clause": "9.4.2"})]),
        store,
    )
    edges = store.graph.get_edges(_id("R-1"), "out", ["cites"])
    assert [e["target_id"] for e in edges] == [derive_doc_id("std", "ISO-9")]
    assert edges[0]["metadata"] == {"clause": "9.4.2", "origin": "source_link"}


def test_resync_replaces_owned_edges_and_keeps_incoming_edges(store):
    do_ingest_document_schema(_item("A", "Alpha text one.", links=[("B", "depends_on", {})]), store)
    do_ingest_document_schema(_item("D", "Delta text.", links=[("A", "depends_on", {})]), store)
    do_ingest_document_schema(_item("A", "Alpha text two.", links=[("C", "depends_on", {})]), store)

    assert _out(store, "A") == {(_id("C"), "depends_on")}
    assert (_id("A"), "depends_on") in _out(store, "D")


def test_doc_type_and_identity_persist_on_node_and_segments(store):
    result = do_ingest_document_schema(
        _item("R-2", "The unit shall report faults.", tags=["domain:safety"], metadata={"status": "Draft"}),
        store,
    )
    node = store.graph.get_node(_id("R-2"))
    assert node["metadata"]["doc_type"] == "requirement"
    assert node["metadata"]["system"] == "cb-test"
    assert node["metadata"]["native_id"] == "R-2"
    assert node["metadata"]["metadata"] == {"status": "Draft"}
    assert node["metadata"]["domain_tags"] == ["domain:safety"]

    tags = store.get_by_uuid(result["segment_ids"][0])["metadata"]["tags"].split(",")
    assert "doctype:requirement" in tags
    assert "domain:safety" in tags


def test_resync_refreshes_document_name(store):
    do_ingest_document_schema(_item("R-3", "Text one.", name="Old name"), store)
    do_ingest_document_schema(_item("R-3", "Text two.", name="New name"), store)
    assert store.graph.get_node(_id("R-3"))["name"] == "New name"


def test_self_link_is_quarantined(store):
    result = do_ingest_document_schema(
        _item("R-4", "The unit shall idle.", links=[("R-4", "depends_on", {})]), store
    )
    assert result["status"] == "quarantined"
    assert any("targets its own item" in e for e in result["errors"])


def test_comma_in_domain_tag_is_quarantined(store):
    result = do_ingest_document_schema(_item("R-5", "The unit shall idle.", tags=["a,b"]), store)
    assert result["status"] == "quarantined"
    assert any("must not contain ','" in e for e in result["errors"])

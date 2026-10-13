from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, SourceLink
from smos.memory.requirement_context import requirement_context
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

SYSTEM = "cb-test"


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_adapter(RequirementsAdapter())
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _ingest(store, native_id, text, *, links=(), tags=(), doc_type="requirement",
            system=SYSTEM, name=None):
    payload = IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=name or native_id,
        blocks=[ContentBlock(block_type="paragraph", text=text)],
        doc_type=doc_type,
        domain_tags=list(tags),
        source_links=[SourceLink(target_native_id=t, edge_type=e, metadata=m) for (t, e, m) in links],
    )
    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "success", result
    return result


S1_TEXT = "Occupants must be protected from harm during an abrupt stop."
S2_TEXT = "The braking controller shall apply full deceleration within 150 ms of a collision signal."
S3_TEXT = "The brake actuator driver shall assert the hydraulic valve line on a collision flag."


def _chain(store):
    _ingest(store, "S1", S1_TEXT)
    _ingest(store, "S2", S2_TEXT, links=[("S1", "depends_on", {})])
    _ingest(store, "S3", S3_TEXT, links=[("S2", "depends_on", {})])


def _by_id(items):
    return {i["id"]: i for i in items}


def test_linked_returns_upstream_and_downstream_with_exact_text(store):
    _chain(store)
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="S2")
    linked = _by_id(result["linked"])

    s1, s3 = derive_doc_id(SYSTEM, "S1"), derive_doc_id(SYSTEM, "S3")
    assert set(linked) == {s1, s3}
    assert linked[s1]["reason"] == "upstream (depends_on)"
    assert linked[s1]["text"] == S1_TEXT
    assert linked[s1]["hops"] == 1
    assert linked[s3]["reason"] == "downstream (depends_on)"
    assert linked[s3]["text"] == S3_TEXT
    assert result["coverage"]["has_upstream"] is True
    assert result["coverage"]["has_downstream"] is True


def test_two_hop_neighbour_is_included_with_hop_count(store):
    _chain(store)
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="S3")
    linked = _by_id(result["linked"])
    s1 = derive_doc_id(SYSTEM, "S1")
    assert linked[s1]["hops"] == 2
    assert [p["edge_type"] for p in linked[s1]["path"]] == ["depends_on", "depends_on"]


def test_parent_of_is_labelled_from_the_childs_point_of_view(store):
    _ingest(store, "P", "The vehicle shall provide a braking function.", links=[("C", "parent_of", {})])
    _ingest(store, "C", "The braking controller shall compute pedal demand.")
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="C")
    item = _by_id(result["linked"])[derive_doc_id(SYSTEM, "P")]
    assert item["reason"] == "parent (parent_of)"


def test_unresolved_link_is_reported_not_listed(store):
    _ingest(store, "A", "The unit shall start up.", links=[("MISSING", "depends_on", {})])
    result = requirement_context(store, system=SYSTEM, native_id="A")
    assert result["linked"] == []
    assert result["coverage"]["unresolved_links"] == ["MISSING"]
    assert result["coverage"]["has_upstream"] is True


def test_cyclic_links_terminate_and_exclude_the_seed(store):
    _ingest(store, "A", "Alpha shall start.", links=[("B", "depends_on", {})])
    _ingest(store, "B", "Bravo shall stop.", links=[("A", "depends_on", {})])
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="A")
    assert [i["id"] for i in result["linked"]] == [derive_doc_id(SYSTEM, "B")]


def test_draft_with_declared_link_resolves_the_target(store):
    _chain(store)
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(
        store,
        text="The controller shall brake hard on impact.",
        system=SYSTEM,
        declared_links=[{"target_native_id": "S1", "edge_type": "depends_on"}],
    )
    linked = _by_id(result["linked"])
    s1 = derive_doc_id(SYSTEM, "S1")
    assert linked[s1]["reason"] == "declared depends_on"
    assert result["coverage"]["has_upstream"] is True
    assert result["query"]["doc_id"] is None


def test_unknown_declared_target_is_reported_unresolved(store):
    result = requirement_context(
        store,
        text="The controller shall brake hard on impact.",
        system=SYSTEM,
        declared_links=[{"target_native_id": "NOPE", "edge_type": "depends_on"}],
    )
    assert result["linked"] == []
    assert result["coverage"]["unresolved_links"] == ["NOPE"]


def test_token_budget_smaller_than_first_item_clips_it(store):
    _chain(store)
    result = requirement_context(store, system=SYSTEM, native_id="S2", token_budget=6)
    # per-section budget = 6 // 3 = 2 tokens = 8 characters
    assert result["linked"], "a section must not come back empty just because the budget is tiny"
    first = result["linked"][0]
    assert first["clipped"] is True
    assert len(first["text"]) <= 8
    assert result["truncated"]["linked"] is True


def test_no_input_is_an_error(store):
    assert "error" in requirement_context(store)


def test_unknown_stored_item_is_an_error(store):
    assert "error" in requirement_context(store, system=SYSTEM, native_id="NOPE")

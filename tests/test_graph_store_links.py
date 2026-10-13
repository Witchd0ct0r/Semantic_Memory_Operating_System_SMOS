from __future__ import annotations

import sqlite3

import pytest

from smos.memory.graph_store import GraphStore


def _graph() -> GraphStore:
    return GraphStore(sqlite3.connect(":memory:", check_same_thread=False))


def test_upsert_node_inserts_then_overwrites_name_and_metadata():
    g = _graph()
    g.upsert_node("d1", "document", "old", metadata={"placeholder": True})
    g.upsert_node("d1", "document", "new", metadata={"doc_type": "requirement"})
    node = g.get_node("d1")
    assert node["name"] == "new"
    assert node["metadata"] == {"doc_type": "requirement"}


def test_add_node_still_ignores_an_existing_row():
    g = _graph()
    g.add_node("d1", "document", "first")
    g.add_node("d1", "document", "second")
    assert g.get_node("d1")["name"] == "first"


def test_get_edges_directions_and_edge_type_filter():
    g = _graph()
    for n in ("a", "b", "c"):
        g.add_node(n, "document", n)
    g.add_edge("a", "b", "depends_on")
    g.add_edge("c", "a", "parent_of")

    assert [(e["source_id"], e["target_id"]) for e in g.get_edges("a", "out")] == [("a", "b")]
    assert [(e["source_id"], e["target_id"]) for e in g.get_edges("a", "in")] == [("c", "a")]
    assert len(g.get_edges("a")) == 2
    assert [e["edge_type"] for e in g.get_edges("a", "both", ["parent_of"])] == ["parent_of"]


def test_get_edges_returns_decoded_metadata():
    g = _graph()
    g.add_node("a", "document", "a")
    g.add_node("b", "document", "b")
    g.add_edge("a", "b", "cites", metadata={"clause": "9.4.2"})
    assert g.get_edges("a", "out")[0]["metadata"] == {"clause": "9.4.2"}


def test_get_edges_rejects_unknown_direction():
    with pytest.raises(ValueError):
        _graph().get_edges("a", "sideways")


def test_remove_owned_edges_removes_only_matching_origin_from_that_source():
    g = _graph()
    for n in ("a", "b", "c", "d"):
        g.add_node(n, "document", n)
    g.add_edge("a", "b", "depends_on", metadata={"origin": "source_link"})
    g.add_edge("a", "c", "contains")
    g.add_edge("d", "a", "depends_on", metadata={"origin": "source_link"})

    assert g.remove_owned_edges("a", "source_link") == 1

    remaining = {(e["source_id"], e["target_id"], e["edge_type"]) for e in g.get_edges("a")}
    assert remaining == {("a", "c", "contains"), ("d", "a", "depends_on")}

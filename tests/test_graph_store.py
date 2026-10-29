import sqlite3

import pytest

from smos.memory.graph_store import GraphStore
from tests.helpers import migrated_graph


@pytest.fixture
def graph() -> GraphStore:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return migrated_graph(conn)


def test_add_node_and_get_node(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    node = graph.get_node("mem1")
    assert node is not None
    assert node["type"] == "memory"
    assert node["name"] == "mem1"


def test_add_node_is_idempotent(graph: GraphStore) -> None:
    graph.add_node("e1", "entity", "JWT", metadata={"kind": "reference"})
    graph.add_node("e1", "entity", "JWT", metadata={"kind": "reference"})
    assert graph.node_count() == 1


def test_get_node_returns_none_for_missing(graph: GraphStore) -> None:
    assert graph.get_node("nope") is None


def test_add_edge_and_count(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("e1", "entity", "JWT")
    graph.add_edge("mem1", "e1", "references")
    assert graph.edge_count() == 1


def test_add_edge_dedupes_same_triple(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("e1", "entity", "JWT")
    graph.add_edge("mem1", "e1", "references")
    graph.add_edge("mem1", "e1", "references")
    assert graph.edge_count() == 1


def test_remove_node_deletes_touching_edges_but_not_other_nodes(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("e1", "entity", "JWT")
    graph.add_edge("mem1", "e1", "references")

    graph.remove_node("mem1")

    assert graph.get_node("mem1") is None
    assert graph.edge_count() == 0
    assert graph.get_node("e1") is not None


def test_metadata_round_trips_as_dict(graph: GraphStore) -> None:
    graph.add_node("e1", "entity", "JWT", metadata={"kind": "reference"})
    node = graph.get_node("e1")
    assert node["metadata"] == {"kind": "reference"}


def test_traverse_finds_two_hop_neighbor(graph: GraphStore) -> None:
    # mem1 --references--> JWT <--references-- mem2
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("mem2", "memory", "mem2")
    graph.add_node("jwt", "entity", "JWT")
    graph.add_edge("mem1", "jwt", "references")
    graph.add_edge("mem2", "jwt", "references")

    result = graph.traverse("mem1", max_hops=2)
    ids = {n["id"] for n in result}
    assert "jwt" in ids
    assert "mem2" in ids
    jwt_hop = next(n["hop"] for n in result if n["id"] == "jwt")
    mem2_hop = next(n["hop"] for n in result if n["id"] == "mem2")
    assert jwt_hop == 1
    assert mem2_hop == 2


def test_traverse_respects_max_hops(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("mem2", "memory", "mem2")
    graph.add_node("jwt", "entity", "JWT")
    graph.add_edge("mem1", "jwt", "references")
    graph.add_edge("mem2", "jwt", "references")

    result = graph.traverse("mem1", max_hops=1)
    ids = {n["id"] for n in result}
    assert ids == {"jwt"}


def test_traverse_unknown_start_returns_empty(graph: GraphStore) -> None:
    assert graph.traverse("nope", max_hops=2) == []


def test_traverse_filters_by_edge_type(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("a", "entity", "A")
    graph.add_node("b", "concept", "B")
    graph.add_edge("mem1", "a", "references")
    graph.add_edge("mem1", "b", "related_to")

    result = graph.traverse("mem1", max_hops=1, edge_types=["references"])
    ids = {n["id"] for n in result}
    assert ids == {"a"}


def test_add_node_document_type_succeeds(graph: GraphStore) -> None:
    graph.add_node("doc1", "document", "doc1")
    node = graph.get_node("doc1")
    assert node is not None
    assert node["type"] == "document"


def test_add_edge_accepts_open_text_edge_type(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("req1", "entity", "REQ-1")
    graph.add_edge("mem1", "req1", "satisfies")
    assert graph.edge_count() == 1


def test_migrates_legacy_graph_edges_check_constraint(tmp_path) -> None:
    db_path = tmp_path / "legacy_edges.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE graph_edges (
            id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            source_id  TEXT NOT NULL,
            target_id  TEXT NOT NULL,
            edge_type  TEXT NOT NULL CHECK(edge_type IN (
                           'references','depends_on','related_to',
                           'implements','contains','evolves_into','generated_from'
                       )),
            weight     REAL DEFAULT 1.0,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, edge_type)
        )
    """)
    conn.execute("INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('mem1','memory','mem1','{}')")
    conn.execute("INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('req1','entity','REQ-1','{}')")
    conn.execute(
        "INSERT INTO graph_edges (source_id, target_id, edge_type) VALUES ('mem1','req1','references')"
    )
    conn.commit()

    store = migrated_graph(conn)

    assert store.edge_count() == 1
    store.add_edge("mem1", "req1", "satisfies")
    assert store.edge_count() == 2


def test_migrates_legacy_graph_nodes_check_constraint(tmp_path) -> None:
    db_path = tmp_path / "legacy_nodes.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('mem1', 'memory', 'mem1', '{}')"
    )
    conn.commit()

    store = migrated_graph(conn)

    old_node = store.get_node("mem1")
    assert old_node is not None
    assert old_node["type"] == "memory"

    store.add_node("doc1", "document", "doc1")
    new_node = store.get_node("doc1")
    assert new_node is not None
    assert new_node["type"] == "document"


def test_add_node_section_type_succeeds(graph: GraphStore) -> None:
    graph.add_node("doc1:section:0", "section", "doc1:section:0")
    node = graph.get_node("doc1:section:0")
    assert node is not None
    assert node["type"] == "section"


def test_migrates_5_1_schema_graph_nodes_check_constraint(tmp_path) -> None:
    """A 5.1-era DB already has 'document' but not 'section' in its CHECK
    constraint — the migration guard must key off 'section', not 'document',
    or it will wrongly consider a 5.1 DB already migrated."""
    db_path = tmp_path / "phase5_1_nodes.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('doc1', 'document', 'doc1', '{}')"
    )
    conn.commit()

    store = migrated_graph(conn)

    old_node = store.get_node("doc1")
    assert old_node is not None
    assert old_node["type"] == "document"

    store.add_node("doc1:section:0", "section", "doc1:section:0")
    new_node = store.get_node("doc1:section:0")
    assert new_node is not None
    assert new_node["type"] == "section"

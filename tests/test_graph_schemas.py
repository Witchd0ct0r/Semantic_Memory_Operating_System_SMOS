import pytest
from pydantic import ValidationError

from smos.memory.schemas import GraphEdge, GraphNode


def test_graph_node_constructs_with_defaults():
    node = GraphNode(id="mem1", type="memory", name="mem1")
    assert node.id == "mem1"
    assert node.metadata == {}
    assert node.created_at is not None


def test_graph_node_rejects_invalid_type():
    with pytest.raises(ValidationError):
        GraphNode(id="x", type="not_a_real_type", name="x")


def test_graph_edge_constructs_with_defaults():
    edge = GraphEdge(source_id="mem1", target_id="entity:JWT", edge_type="references")
    assert edge.source_id == "mem1"
    assert edge.target_id == "entity:JWT"
    assert edge.weight == 1.0
    assert edge.id  # auto-generated


def test_graph_edge_rejects_invalid_edge_type():
    with pytest.raises(ValidationError):
        GraphEdge(source_id="a", target_id="b", edge_type="not_a_real_edge_type")


def test_graph_node_metadata_round_trips():
    node = GraphNode(id="e1", type="entity", name="JWT", metadata={"kind": "reference"})
    assert node.model_dump()["metadata"] == {"kind": "reference"}


def test_graph_node_accepts_document_type():
    node = GraphNode(id="doc1", type="document", name="doc1")
    assert node.type == "document"

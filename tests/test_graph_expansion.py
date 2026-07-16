from unittest.mock import MagicMock

from smos.compression.context_builder import build_compressed_context
from smos.memory.bm25_index import BM25Index


def _store_with(memories: list[dict], graph_neighbors: list[dict]):
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories])
    store.bm25 = bm25
    store.graph.traverse.return_value = graph_neighbors
    all_records = {m["id"]: m for m in memories}
    for n in graph_neighbors:
        if n["type"] == "memory" and "record" in n:
            all_records[n["id"]] = n["record"]
    store.get_by_uuid.side_effect = lambda uid: all_records.get(uid)
    return store


def test_graph_expansion_adds_linked_memory_not_found_by_rrf():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    linked_record = {"id": "id2", "content": "completely different wording, linked via graph", "metadata": {"tags": []}}
    graph_neighbors = [
        {"id": "id2", "type": "memory", "name": "id2", "metadata": {}, "hop": 2, "record": linked_record},
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert "id2" in result.sources


def test_graph_expansion_skips_non_memory_nodes():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    graph_neighbors = [
        {"id": "entity:JWTValidator", "type": "entity", "name": "JWTValidator", "metadata": {}, "hop": 1},
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert "entity:JWTValidator" not in result.sources


def test_graph_expansion_skips_nodes_already_in_top():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    graph_neighbors = [
        {"id": "id1", "type": "memory", "name": "id1", "metadata": {}, "hop": 1},
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert result.sources.count("id1") == 1


def test_graph_expansion_caps_at_two_extra_memories():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    graph_neighbors = [
        {
            "id": f"id{i}", "type": "memory", "name": f"id{i}", "metadata": {}, "hop": 2,
            "record": {"id": f"id{i}", "content": f"linked memory {i}", "metadata": {"tags": []}},
        }
        for i in range(2, 6)  # 4 candidates offered, only 2 should be added
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert len(result.sources) <= 3  # 1 RRF result + at most 2 graph extras


def test_no_graph_neighbors_leaves_result_unchanged():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    store = _store_with([top_result], [])

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert result.sources == ["id1"]

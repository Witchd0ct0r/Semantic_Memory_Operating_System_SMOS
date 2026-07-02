import pytest
from unittest.mock import MagicMock


def _make_store_with_memories(memories: list[dict]):
    """Helper: mock VectorStore for context builder tests."""
    from smos.memory.bm25_index import BM25Index
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories])
    store.bm25 = bm25
    store.get_by_uuid.side_effect = lambda uid: next(
        (m for m in memories if m["id"] == uid), None
    )
    return store


def test_exact_term_memory_ranks_high():
    """BM25 should boost exact-match memories above pure semantic matches."""
    from smos.compression.context_builder import build_compressed_context

    memories = [
        {"id": "id1", "content": "JWTValidator class handles token validation", "distance": 0.3, "tags": []},
        {"id": "id2", "content": "authentication is important for security", "distance": 0.1, "tags": []},
        {"id": "id3", "content": "the middleware pipeline processes requests", "distance": 0.25, "tags": []},
    ]
    store = _make_store_with_memories(memories)

    result = build_compressed_context("JWTValidator", k=2, store=store)
    assert result.confidence > 0.0
    # id1 has the exact term — should appear in sources
    assert "id1" in result.sources


def test_hybrid_returns_compressedcontext():
    from smos.compression.context_builder import build_compressed_context
    from smos.memory.schemas import CompressedContext

    memories = [
        {"id": "id1", "content": "test content for retrieval", "distance": 0.2, "tags": []},
    ]
    store = _make_store_with_memories(memories)
    result = build_compressed_context("test content", k=1, store=store)
    assert isinstance(result, CompressedContext)

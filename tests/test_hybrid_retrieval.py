import pytest
from unittest.mock import MagicMock


def _make_store_with_memories(memories: list[dict], extra_bm25_docs: list[dict] | None = None):
    """Helper: mock VectorStore for context builder tests.

    extra_bm25_docs: memories indexed into BM25 but NOT included in the
    query_domain() return value — simulates a memory that only BM25 finds
    (never surfaced by vector search). Each dict should match the real
    store.get_many() record shape: {"id", "content", "metadata"} —
    notably no top-level "distance" or "tags" keys, unlike vector-sourced
    memory dicts.
    """
    from smos.memory.bm25_index import BM25Index
    extra_bm25_docs = extra_bm25_docs or []
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories + extra_bm25_docs])
    store.bm25 = bm25
    store.graph.traverse.return_value = []
    store.get_summaries.return_value = {}
    all_records = memories + extra_bm25_docs
    store.get_many.side_effect = lambda uids: {m["id"]: m for m in all_records if m["id"] in uids}
    return store


def test_exact_term_memory_ranks_high():
    """BM25 should boost exact-match memories above pure semantic matches."""
    from smos.compression.context_builder import build_context

    memories = [
        {"id": "id1", "content": "JWTValidator class handles token validation", "distance": 0.3, "tags": []},
        {"id": "id2", "content": "authentication is important for security", "distance": 0.1, "tags": []},
        {"id": "id3", "content": "the middleware pipeline processes requests", "distance": 0.25, "tags": []},
    ]
    store = _make_store_with_memories(memories)

    result = build_context("JWTValidator", k=2, store=store)
    assert result.confidence > 0.0
    # id1 has the exact term — should appear in sources
    assert "id1" in result.sources


def test_hybrid_returns_retrieved_context():
    from smos.compression.context_builder import build_context
    from smos.memory.schemas import RetrievedContext

    memories = [
        {"id": "id1", "content": "test content for retrieval", "distance": 0.2, "tags": []},
    ]
    store = _make_store_with_memories(memories)
    result = build_context("test content", k=1, store=store)
    assert isinstance(result, RetrievedContext)


def test_bm25_only_hit_is_hydrated_via_get_many():
    """A memory found only by BM25 (absent from vector_results) must be
    hydrated via store.get_many() and survive missing "distance"/"tags"
    keys — get_many() results don't carry a FAISS distance the way
    vector-sourced dicts do. Without the `.get("distance", 1.0)` default in
    build_context(), this crashes with KeyError on bare
    `c["distance"]` access.
    """
    from smos.compression.context_builder import build_context

    memories = [
        {"id": "id1", "content": "completely unrelated filler text about weather", "distance": 0.5, "tags": []},
    ]
    # This record is indexed into BM25 but deliberately excluded from the
    # query_domain() mock return — it is only discoverable via BM25.
    # Shape matches the real get_many() record: no "distance" key.
    bm25_only_record = {
        "id": "bm25-only-id",
        "content": "JWTValidator token authentication middleware",
        "metadata": {"tags": []},
    }
    # Extra filler docs solely to give the corpus enough documents (N=4) for
    # BM25 IDF to be meaningfully non-zero: with only 2 documents, a term
    # appearing in exactly 1 of them always has idf == log(1.5/1.5) == 0
    # (rank_bm25's formula), so "jwtvalidator" would score 0.0 even on the
    # matching doc and get filtered out as zero-relevance padding by
    # build_context()'s BM25 zero-score filter.
    filler_docs = [
        {"id": "filler-1", "content": "database migration script executed successfully", "metadata": {"tags": []}},
        {"id": "filler-2", "content": "caching layer improves response latency", "metadata": {"tags": []}},
    ]
    store = _make_store_with_memories(memories, extra_bm25_docs=[bm25_only_record, *filler_docs])

    result = build_context("JWTValidator", k=2, store=store)

    # The hydration branch (store.get_many) must actually have been
    # exercised — this is what the previous tests failed to cover.
    assert store.get_many.call_count >= 1
    assert "bm25-only-id" in result.sources

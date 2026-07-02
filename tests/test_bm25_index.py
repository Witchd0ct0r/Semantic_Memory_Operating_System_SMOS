import pytest
from smos.memory.bm25_index import BM25Index


def test_build_and_query_returns_ranked_results():
    idx = BM25Index()
    idx.build([
        ("id1", "JWT token authentication middleware"),
        ("id2", "database connection pooling postgres"),
        ("id3", "JWT refresh token expiry validation"),
    ])
    results = idx.query("JWT token", k=3)
    ids = [r[0] for r in results]
    assert ids[0] in ("id1", "id3"), "JWT-containing doc should rank first"
    assert "id2" not in ids[:2], "postgres doc should not rank top-2 for JWT query"


def test_update_adds_new_document():
    idx = BM25Index()
    idx.build([("id1", "authentication middleware")])
    idx.update("id2", "BM25 retrieval ranking algorithm")
    results = idx.query("BM25 ranking", k=2)
    assert results[0][0] == "id2"


def test_remove_excludes_document():
    idx = BM25Index()
    idx.build([
        ("id1", "JWT authentication"),
        ("id2", "JWT token refresh"),
    ])
    idx.remove("id1")
    results = idx.query("JWT authentication", k=5)
    ids = [r[0] for r in results]
    assert "id1" not in ids


def test_query_empty_index_returns_empty():
    idx = BM25Index()
    idx.build([])
    assert idx.query("anything", k=5) == []


def test_scores_are_floats_between_zero_and_positive():
    idx = BM25Index()
    idx.build([("id1", "test content"), ("id2", "other content")])
    results = idx.query("test", k=2)
    for _, score in results:
        assert isinstance(score, float)
        assert score >= 0.0

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


def test_add_batch_adds_multiple_documents_and_is_queryable():
    idx = BM25Index()
    idx.build([("id1", "existing baseline document")])
    idx.add_batch([
        ("id2", "unique_batch_token_alpha implementation"),
        ("id3", "unique_batch_token_alpha usage guide"),
        ("id4", "completely unrelated database content"),
    ])
    results = idx.query("unique_batch_token_alpha", k=5)
    ids = [r[0] for r in results]
    assert "id2" in ids
    assert "id3" in ids
    # existing doc from build() must remain queryable after add_batch
    baseline = idx.query("existing baseline document", k=5)
    assert any(r[0] == "id1" for r in baseline)


def test_add_batch_skips_uuids_already_present():
    idx = BM25Index()
    idx.build([("id1", "original content for id1")])
    idx.add_batch([("id1", "should not overwrite"), ("id2", "new_doc_marker content")])
    results = idx.query("new_doc_marker", k=5)
    assert any(r[0] == "id2" for r in results)
    # id1 content should be unchanged (still only one instance in the index)
    idx.remove("id2")
    remaining = idx.query("original content for id1", k=5)
    assert any(r[0] == "id1" for r in remaining)


def test_add_batch_empty_list_is_noop():
    idx = BM25Index()
    idx.build([("id1", "some content")])
    idx.add_batch([])
    results = idx.query("some content", k=5)
    assert any(r[0] == "id1" for r in results)

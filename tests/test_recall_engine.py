from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smos.memory.recall_engine import recall
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str, timestamp: datetime | None = None, **kwargs) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=timestamp or datetime.utcnow(), **kwargs)


def test_get_by_timestamp_range_filters_correctly(store: VectorStore) -> None:
    old_id = store.store(_mem("Old fact.", timestamp=datetime.utcnow() - timedelta(days=10)))
    new_id = store.store(_mem("New fact.", timestamp=datetime.utcnow()))
    since = (datetime.utcnow() - timedelta(days=1)).isoformat()
    rows = store.get_by_timestamp_range(since=since)
    ids = {r["id"] for r in rows}
    assert new_id in ids
    assert old_id not in ids


def test_get_by_timestamp_range_unbounded_returns_all(store: VectorStore) -> None:
    store.store(_mem("Fact one."))
    store.store(_mem("Fact two."))
    rows = store.get_by_timestamp_range()
    assert len(rows) == 2


def test_recall_unknown_strategy_returns_error(store: VectorStore) -> None:
    result = recall("query", "bogus", 5, "", "", "", "", store)
    assert result == {"error": "unknown strategy: bogus"}


def test_recall_hybrid_matches_build_compressed_context(store: VectorStore) -> None:
    store.store(_mem("PostgreSQL uses MVCC for isolation."))
    from smos.compression.context_builder import build_compressed_context
    direct = build_compressed_context("database isolation", 5, store).model_dump()
    via_recall = recall("database isolation", "hybrid", 5, "", "", "", "", store)
    assert via_recall["summary"] == direct["summary"]
    assert via_recall["sources"] == direct["sources"]


def test_recall_bumps_recall_count(store: VectorStore) -> None:
    mem_id = store.store(_mem("PostgreSQL uses MVCC for isolation."))
    recall("database isolation", "hybrid", 5, "", "", "", "", store)
    row = store._db.execute(
        "SELECT recall_count FROM memories WHERE uuid = ?", (mem_id,)
    ).fetchone()
    assert row[0] == 1


def test_recall_repo_filters_by_component(store: VectorStore) -> None:
    auth_id = store.store(
        MemoryObject(type="doc", content="Auth service handles login flows for users.", tags=["auth-service"])
    )
    billing_id = store.store(
        MemoryObject(type="doc", content="Billing service handles invoices for customers.", tags=["billing-service"])
    )
    result = recall("service behavior", "repo", 5, "", "", "", "auth-service", store)
    assert auth_id in result["sources"]
    assert billing_id not in result["sources"]


def test_recall_temporal_respects_since_bound(store: VectorStore) -> None:
    old_id = store.store(_mem("Old fact about caching.", timestamp=datetime.utcnow() - timedelta(days=10)))
    new_id = store.store(_mem("New fact about caching.", timestamp=datetime.utcnow()))
    since = (datetime.utcnow() - timedelta(days=1)).isoformat()
    result = recall("caching", "temporal", 5, since, "", "", "", store)
    assert new_id in result["sources"]
    assert old_id not in result["sources"]


def test_recall_temporal_no_matches_returns_uncertain(store: VectorStore) -> None:
    since = (datetime.utcnow() + timedelta(days=1)).isoformat()  # future — nothing qualifies
    result = recall("anything", "temporal", 5, since, "", "", "", store)
    assert result["sources"] == []
    assert result["mode"] == "uncertain"


def test_recall_chain_follows_evolves_into_edges(store: VectorStore) -> None:
    m1 = store.store(_mem("Version one of the design doc."))
    m2 = store.store(_mem("Version two of the design doc."))
    store.graph.add_edge(m1, m2, "evolves_into")
    result = recall("", "chain", 2, "", "", m1, "", store)
    assert m2 in result["sources"]
    assert result["mode"] == "extractive"


def test_recall_chain_ignores_references_edges(store: VectorStore) -> None:
    m1 = store.store(_mem("The `Foo` class is defined here."))
    result = recall("", "chain", 2, "", "", m1, "", store)
    assert result["sources"] == []

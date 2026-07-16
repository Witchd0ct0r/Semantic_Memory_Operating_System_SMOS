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

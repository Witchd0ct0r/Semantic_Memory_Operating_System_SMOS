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

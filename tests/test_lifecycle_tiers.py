from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smos.memory.lifecycle import LifecycleManager
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _seed(store: VectorStore, n: int) -> list[str]:
    return [
        store.store(MemoryObject(type="doc", content=f"Fact number {i} about a topic."))
        for i in range(n)
    ]


def _set_recall(store: VectorStore, uuid: str, count: int, days_ago: float | None) -> None:
    last_recalled = (
        (datetime.utcnow() - timedelta(days=days_ago)).isoformat() if days_ago is not None else None
    )
    store._db.execute(
        "UPDATE memories SET recall_count = ?, last_recalled = ? WHERE uuid = ?",
        (count, last_recalled, uuid),
    )
    store._db.commit()


def test_promote_tiers_below_min_size_is_noop(store: VectorStore) -> None:
    ids = _seed(store, 3)
    LifecycleManager(store)._promote_tiers()
    for uuid in ids:
        assert store.get_by_uuid(uuid)["metadata"]["tier"] == "hot"


def test_promote_tiers_hot_by_recency(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=0, days_ago=1)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "hot"


def test_promote_tiers_hot_by_recall_count(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=11, days_ago=60)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "hot"


def test_promote_tiers_warm_by_recency(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=0, days_ago=20)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "warm"


def test_promote_tiers_cold_when_never_recalled(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=0, days_ago=None)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "cold"

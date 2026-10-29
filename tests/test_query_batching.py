from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    s = VectorStore(persist_path=tmp_path / "data")
    for i in range(12):
        s.store(MemoryObject(type="doc", content=f"clause number {i} about retention of records"))
    return s


def _memory_selects(store: VectorStore, action) -> int:
    seen: list[str] = []
    store._db.set_trace_callback(lambda sql: seen.append(sql) if "FROM memories" in sql else None)
    try:
        action()
    finally:
        store._db.set_trace_callback(None)
    return len(seen)


def test_query_reads_all_hits_with_one_select(store: VectorStore) -> None:
    results = []
    assert _memory_selects(store, lambda: results.extend(store.query("retention of records", k=10))) == 1
    assert len(results) == 10
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_get_many_is_one_select_and_skips_missing(store: VectorStore) -> None:
    ids = store.get_all_uuids()[:5]
    found: dict = {}
    assert _memory_selects(store, lambda: found.update(store.get_many(ids + ["missing", ids[0]]))) == 1
    assert set(found) == set(ids)
    assert found[ids[0]] == store.get_by_uuid(ids[0])


def test_get_many_chunks_beyond_the_sql_variable_limit(store: VectorStore, monkeypatch) -> None:
    monkeypatch.setattr(VectorStore, "_SQL_VARIABLE_CHUNK", 4)
    ids = store.get_all_uuids()
    assert len(ids) == 12
    found: dict = {}
    assert _memory_selects(store, lambda: found.update(store.get_many(ids))) == 3  # ceil(12 / 4)
    assert set(found) == set(ids)
    results: list = []
    assert _memory_selects(store, lambda: results.extend(store.query("retention of records", k=12))) == 3
    assert len(results) == 12

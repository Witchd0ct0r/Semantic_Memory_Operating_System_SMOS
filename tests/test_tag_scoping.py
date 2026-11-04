from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.bm25_index import BM25Index
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str, tags: list[str]) -> MemoryObject:
    return MemoryObject(type="doc", content=content, tags=tags)


# ─── Task 3: pre-filtered search ─────────────────────────────────────────────

def test_narrow_tag_still_returns_k_hits(store: VectorStore) -> None:
    store.store_batch([_mem(f"lead cadmium exposure limits for workers number {i}", ["big"]) for i in range(60)])
    rare = store.store_batch([_mem(f"noise vibration thresholds for machines number {i}", ["rare"]) for i in range(3)])
    hits = store.query_domain("lead cadmium exposure limits", k=3, domain_tags=["rare"])
    assert sorted(h["id"] for h in hits) == sorted(rare)


def test_bm25_allowed_excludes_others() -> None:
    index = BM25Index()
    filler = [(f"f{i}", f"blue boat number {i}") for i in range(8)]  # keeps idf of "red" positive
    index.build([("u1", "red apple"), ("u2", "red car"), ("u3", "green apple")] + filler)
    assert {u for u, s in index.query("red", 5) if s > 0} == {"u1", "u2"}
    only = index.query("red", 5, allowed=frozenset({"u2"}))
    assert [u for u, _ in only] == ["u2"] and only[0][1] > 0
    assert index.query("red", 5, allowed=frozenset()) == []


def test_cosine_scores_match_vector_search(store: VectorStore) -> None:
    store.store_batch([_mem("password rotation policy for service accounts", []),
                       _mem("brake pad wear measurement procedure", [])])
    hits = store.query_domain("password rotation policy", k=2)
    scores = store.cosine_scores("password rotation policy", [h["id"] for h in hits])
    for h in hits:
        assert scores[h["id"]] == pytest.approx(h["score"], abs=1e-5)

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


# ─── Task 4: build_context scoping, totals, paging ───────────────────────────

from smos.compression.context_builder import build_context


def _ids(ctx) -> list[str]:
    return [item.id for item in ctx.results]


def _three_norms(store: VectorStore) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for norm in ("norm:A", "norm:B", "norm:C"):
        groups[norm] = store.store_batch([
            _mem(f"quality requirement clause {i} about testing evidence zetaword {norm}", [norm])
            for i in range(15)
        ])
    return groups


def test_scoped_query_never_returns_out_of_scope(store: VectorStore) -> None:
    groups = _three_norms(store)
    ctx = build_context("zetaword quality requirement clause testing", 20, store, tags=["norm:A"])
    assert ctx.results, "scoped query returned nothing"
    assert set(_ids(ctx)) <= set(groups["norm:A"])
    assert all("norm:A" in item.metadata["tags"].split(",") for item in ctx.results)


def test_no_fallback_for_empty_scope(store: VectorStore) -> None:
    _three_norms(store)
    ctx = build_context("quality requirement clause testing", 5, store, tags=["nobody"])
    assert ctx.results == [] and ctx.unknown_tags == ["nobody"] and ctx.total == 0


def test_tags_all_and_none_combine(store: VectorStore) -> None:
    ab = store.store(_mem("shared clause about brake testing procedure", ["a", "b"]))
    a = store.store(_mem("only first clause about brake testing procedure", ["a"]))
    b = store.store(_mem("only second clause about brake testing procedure", ["b"]))
    q = "clause about brake testing procedure"
    assert set(_ids(build_context(q, 10, store, tags=["a"], tags_none=["b"]))) == {a}
    assert set(_ids(build_context(q, 10, store, tags_all=["a", "b"]))) == {ab}
    assert set(_ids(build_context(q, 10, store, tags=["a", "b"]))) == {ab, a, b}
    unknown = build_context(q, 10, store, tags_all=["a", "typo"])
    assert unknown.results == [] and unknown.unknown_tags == ["typo"]


def test_total_le_k_means_complete(store: VectorStore) -> None:
    groups = _three_norms(store)
    q = "quality requirement clause testing"
    full = build_context(q, 20, store, tags=["norm:A"])
    assert full.total == 15 and set(_ids(full)) == set(groups["norm:A"])
    part = build_context(q, 10, store, tags=["norm:A"])
    assert part.total == 15 and len(part.results) == 10


def test_paging_is_stable(store: VectorStore) -> None:
    _three_norms(store)
    q = "quality requirement clause testing evidence"
    whole = _ids(build_context(q, 15, store, tags=["norm:B"]))
    pages: list[str] = []
    for offset in (0, 5, 10):
        pages += _ids(build_context(q, 5, store, tags=["norm:B"], offset=offset))
    assert pages == whole and len(set(pages)) == 15


def test_offset_past_end_is_empty_with_total(store: VectorStore) -> None:
    _three_norms(store)
    ctx = build_context("quality requirement clause testing", 5, store, tags=["norm:C"], offset=40)
    assert ctx.results == [] and ctx.total == 15 and ctx.offset == 40


def test_blank_and_empty_tags_mean_no_filter(store: VectorStore) -> None:
    _three_norms(store)
    q = "quality requirement clause testing"
    plain = build_context(q, 5, store)
    blank = build_context(q, 5, store, tags=[""], tags_all=[], tags_none=["  "])
    assert _ids(blank) == _ids(plain) and plain.total == blank.total == 45


def test_scores_are_always_numbers(store: VectorStore) -> None:
    _three_norms(store)
    store.store(_mem("Qwertzuiop unrelated marker sentence about gardening tools", []))
    ctx = build_context("Qwertzuiop quality requirement", 10, store)
    assert ctx.results and all(isinstance(item.score, float) for item in ctx.results)

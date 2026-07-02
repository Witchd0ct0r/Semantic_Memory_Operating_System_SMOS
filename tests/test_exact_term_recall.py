"""Exact-term retrieval regression gate.

Task 4 of the BM25 hybrid retrieval plan. The plan's brief called for a
`test_exact_term_retrieval_quality(vector_store_with_corpus)` function added to
`tests/bench_retrieval.py`, run via `pytest tests/bench_retrieval.py -v -k
"exact_term"`. That target does not work in this repo:

1. `pyproject.toml` restricts pytest collection to `python_files = ["test_*.py"]`,
   so `bench_retrieval.py` is never collected by a normal `pytest tests/` run —
   it (like the other `tests/bench_*.py` files) is a standalone narrative
   benchmark script invoked via `python tests/bench_retrieval.py`, not a
   pytest-collected test module.
2. No `vector_store_with_corpus` fixture exists anywhere in this repo.

This file implements the brief's actual test logic — seed exact-identifier
memories plus semantically-similar noise, query via `build_compressed_context`
for each exact term, assert recall >= 0.80 — as a real `test_*.py` module so
pytest actually collects and runs it as part of the CI-equivalent suite,
making it a genuine regression gate. It builds a real `VectorStore` (backed by
a real `BM25Index` and real FAISS/embeddings), following the fixture pattern
used in `tests/test_vector_store.py`, rather than the `MagicMock`-based store
used for unit tests in `tests/test_hybrid_retrieval.py` — this test exercises
the full real retrieval stack end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smos.compression.context_builder import build_compressed_context
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_exact_term_retrieval_quality(store: VectorStore) -> None:
    """
    Hybrid retrieval must recall at least 80% of memories when queried
    by an exact function/class name that appears in their content.
    Regression gate: ensures BM25 is active and improving exact-term recall.
    """
    # Seed memories with exact identifiers
    exact_terms = [
        ("AuthMiddleware", "AuthMiddleware validates JWT tokens in the request pipeline"),
        ("DatabaseConnectionPool", "DatabaseConnectionPool manages postgres connections with retry logic"),
        ("RateLimiter", "RateLimiter enforces per-user request quotas using a sliding window"),
    ]
    ids = {}
    for term, content in exact_terms:
        mem = MemoryObject(type="doc", content=content)
        mid = store.store(mem)
        ids[term] = mid

    # Also add noise memories that are semantically similar but don't contain the exact term
    noise = [
        "middleware validates incoming requests for authorization",
        "connection management handles retries and timeouts",
        "request limiting enforces usage quotas",
    ]
    for n in noise:
        store.store(MemoryObject(type="doc", content=n))

    hits = 0
    for term, _ in exact_terms:
        result = build_compressed_context(term, k=3, store=store)
        if ids[term] in result.sources:
            hits += 1

    recall = hits / len(exact_terms)
    assert recall >= 0.80, f"Exact-term recall {recall:.2f} < 0.80 — BM25 may not be active"

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
memories plus semantically-similar noise, query via `build_context`
for each exact term, assert recall >= 0.80 — as a real `test_*.py` module so
pytest actually collects and runs it as part of the CI-equivalent suite,
making it a genuine regression gate. It builds a real `VectorStore` (backed by
a real `BM25Index` and real FAISS/embeddings), following the fixture pattern
used in `tests/test_vector_store.py`, rather than the `MagicMock`-based store
used for unit tests in `tests/test_hybrid_retrieval.py` — this test exercises
the full real retrieval stack end to end.

Why the corpus looks the way it does
-------------------------------------
An earlier version of this test seeded only 3 short "noise" documents per
exact term. That corpus recalled 1.00 even with BM25 fully disabled
(vector-only retrieval), because `all-MiniLM-L6-v2` embeddings correlate
strongly with literal substring/token overlap: a query that *is* the exact
identifier (e.g. "AuthMiddleware") cosine-matches a document that *contains*
that identifier almost perfectly, regardless of how much unrelated noise
surrounds it. That made the test pass identically whether or not BM25 hybrid
retrieval was active — i.e. it wasn't testing what it claimed to test.

Empirically (see noise generation below), pushing an exact-term document
out of vector-only's short-list requires several dozen *paraphrase* noise
documents per concept that reuse the identifier's constituent words in
split/spaced form (e.g. "the database connection pool" instead of
"DatabaseConnectionPool") — this exploits the fact that BM25 tokenizes the
camelCase identifier as one indivisible token (`_tokenize` splits on
non-word characters, so "DatabaseConnectionPool" stays a single token,
distinct from "database", "connection", "pool" as separate tokens), while
the embedding model does not preserve that distinction anywhere near as
sharply. Even with 1000+ tuned paraphrases, one of the three concepts here
("RateLimiter") never leaves vector rank #1 — that specific identifier's
embedding is apparently unusually self-similar in this model — which is
itself evidence that corpus-difficulty tuning alone is an unreliable,
model-version-dependent way to gate BM25 regressions. That is why this file
also carries an explicit hybrid-vs-vector-only A/B comparison
(`test_hybrid_recall_beats_vector_only`) that structurally cannot pass
unless BM25 is actually contributing, independent of corpus tuning luck.
"""

from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from smos.compression.context_builder import build_context
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


# ---------------------------------------------------------------------------
# Noise corpus generation
#
# Each concept gets `_NOISE_PER_CONCEPT` deterministically-generated paraphrase
# sentences that discuss the same topic as the exact-term document but never
# contain the literal joined identifier. Sentences are built by combinatorial
# template expansion (subject x verb x object x tail) with a fixed random
# seed per concept, so the corpus is reproducible across runs.
# ---------------------------------------------------------------------------

_NOISE_PER_CONCEPT = 60


def _generate_noise(
    subjects: list[str],
    verbs: list[str],
    objects: list[str],
    tails: list[str],
    seed: int,
    n: int,
) -> list[str]:
    rng = random.Random(seed)
    combos = list(itertools.product(subjects, verbs, objects, tails))
    rng.shuffle(combos)
    phrases = [f"{s} {v} {o} {t}" for s, v, o, t in combos]
    # dict.fromkeys dedupes while preserving order (templates can collide).
    return list(dict.fromkeys(phrases))[:n]


_AUTH_NOISE = _generate_noise(
    subjects=[
        "the middleware", "a security filter", "the gateway", "the auth layer",
        "the request handler", "the identity service", "the login flow",
        "the access layer", "the credential checker", "the session manager",
    ],
    verbs=[
        "validates", "verifies", "checks", "inspects", "confirms", "parses",
        "screens", "authenticates", "authorizes", "enforces",
    ],
    objects=[
        "JWT tokens", "bearer tokens", "auth headers", "access tokens",
        "session credentials", "signed tokens", "user identity",
        "authorization headers", "login credentials", "request tokens",
    ],
    tails=[
        "before routing the request", "for every incoming call",
        "at the start of the pipeline", "prior to handler execution",
        "on each API request", "to prevent unauthorized access",
        "as part of the security stack", "before forwarding to the backend",
        "for the gateway layer", "to enforce access control",
    ],
    seed=42,
    n=_NOISE_PER_CONCEPT,
)

_DB_NOISE = _generate_noise(
    subjects=[
        "the database connection pool", "a connection pool for the database",
        "the postgres connection pool", "the pooled database connections",
        "the connection pool manager", "the database pool subsystem",
        "the pool of database connections", "the postgres connection manager",
        "the shared connection pool", "the database session pool",
    ],
    verbs=[
        "manages", "recycles", "maintains", "pools", "reuses", "tracks",
        "handles", "oversees", "coordinates", "allocates",
    ],
    objects=[
        "database connections", "postgres sockets", "open sessions",
        "pooled handles", "client connections", "db sessions",
        "connection slots", "network sockets", "reusable links",
        "active sessions",
    ],
    tails=[
        "with automatic retry logic", "to reduce query overhead",
        "across concurrent workers", "using exponential backoff",
        "for the backend service", "to avoid reconnect cost",
        "during high traffic periods", "for connection reuse",
        "to minimize latency", "under heavy load",
    ],
    seed=43,
    n=_NOISE_PER_CONCEPT,
)

_RATE_LIMITER_NOISE = _generate_noise(
    subjects=[
        "the rate limiter", "a rate limiter", "the request rate limiter",
        "a per-user rate limiter", "the rate limiting component",
        "the sliding window rate limiter", "the API rate limiter",
        "the client rate limiter", "the rate limit enforcer",
        "the quota rate limiter",
    ],
    verbs=["enforces", "applies", "sets", "limits with", "uses", "implements"],
    objects=[
        "per-user request quotas", "request quotas per user",
        "a sliding window of request quotas", "per-user quotas for requests",
    ],
    tails=[
        "using a sliding window", "with a sliding window",
        "via a sliding window algorithm", "based on a sliding request window",
        "using a sliding time window for quotas",
    ],
    seed=99,
    n=_NOISE_PER_CONCEPT,
)

# (exact term, document content containing it, noise corpus for that concept)
_EXACT_TERMS = [
    (
        "AuthMiddleware",
        "AuthMiddleware validates JWT tokens in the request pipeline",
        _AUTH_NOISE,
    ),
    (
        "DatabaseConnectionPool",
        "DatabaseConnectionPool manages postgres connections with retry logic",
        _DB_NOISE,
    ),
    (
        "RateLimiter",
        "RateLimiter enforces per-user request quotas using a sliding window",
        _RATE_LIMITER_NOISE,
    ),
]


def _seed_corpus(store: VectorStore) -> dict[str, str]:
    """Seed exact-identifier memories plus their paraphrase noise. Returns
    {term: memory_id} for the exact-identifier memories."""
    ids: dict[str, str] = {}
    for term, content, _noise in _EXACT_TERMS:
        ids[term] = store.store(MemoryObject(type="doc", content=content))
    for _term, _content, noise in _EXACT_TERMS:
        for n in noise:
            store.store(MemoryObject(type="doc", content=n))
    return ids


def _recall(store: VectorStore, ids: dict[str, str]) -> float:
    hits = 0
    for term, _content, _noise in _EXACT_TERMS:
        result = build_context(term, k=3, store=store)
        if ids[term] in result.sources:
            hits += 1
    return hits / len(_EXACT_TERMS)


def test_exact_term_retrieval_quality(store: VectorStore) -> None:
    """
    Hybrid retrieval must recall at least 80% of memories when queried
    by an exact function/class name that appears in their content, even
    against a corpus stacked with semantically-similar noise documents
    (60 per concept, 183 total) that do not contain the literal identifier.
    Regression gate: ensures BM25 is active and improving exact-term recall.
    """
    ids = _seed_corpus(store)
    recall = _recall(store, ids)
    assert recall >= 0.80, f"Exact-term recall {recall:.2f} < 0.80 — BM25 may not be active"


def test_hybrid_recall_beats_vector_only(store: VectorStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Structural regression gate: hybrid (BM25 + vector) retrieval must
    outperform vector-only retrieval on the same exact-term corpus.

    Unlike `test_exact_term_retrieval_quality`, this does not rely on corpus
    difficulty alone (which is sensitive to embedding-model/FAISS-version
    drift — see module docstring). It directly measures the *causal*
    contribution of BM25 by bypassing it (`store.bm25.query` monkeypatched to
    return no results, simulating BM25 being disabled/broken) and comparing
    recall against the same real, unpatched hybrid path on an identical
    corpus. This test cannot pass unless BM25 is actually contributing to
    exact-term recall, regardless of how the corpus or embedding model
    evolves.
    """
    ids = _seed_corpus(store)

    hybrid_recall = _recall(store, ids)

    monkeypatch.setattr(store.bm25, "query", lambda text, k: [])
    vector_only_recall = _recall(store, ids)

    assert hybrid_recall >= 0.80, (
        f"Hybrid recall {hybrid_recall:.2f} < 0.80 with BM25 active — regression"
    )
    assert vector_only_recall < 0.80, (
        f"Vector-only recall {vector_only_recall:.2f} >= 0.80 — corpus no longer "
        "discriminates; BM25's contribution can't be observed"
    )
    assert hybrid_recall > vector_only_recall, (
        f"Hybrid recall {hybrid_recall:.2f} did not beat vector-only recall "
        f"{vector_only_recall:.2f} — BM25 is not improving exact-term recall"
    )

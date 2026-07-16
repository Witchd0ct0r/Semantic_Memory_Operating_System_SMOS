# BM25 Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire BM25Okapi into SMOS's retrieval pipeline alongside FAISS vector search, producing a hybrid score via Reciprocal Rank Fusion that materially improves exact-term retrieval quality (function names, error codes, identifiers).

**Architecture:** Add a `BM25Index` class in `smos/memory/bm25_index.py` that maintains an in-memory BM25 corpus rebuilt from SQLite on startup and updated on every insert. Modify `context_builder.py` to run BM25 alongside FAISS and merge via RRF before reranking. The existing MCP tool surface is unchanged — this is a pure internal pipeline improvement.

**Tech Stack:** `rank_bm25` (BM25Okapi), existing SQLite + FAISS stack, Python stdlib `re` for tokenization.

## Global Constraints

- `rank_bm25` is the only new dependency (`pip install rank-bm25`)
- All existing benchmarks must pass unchanged: `bench_retrieval`, `bench_security`, `bench_mcp_tools`, `bench_performance`, `bench_compression`
- All existing MCP tools remain functional — no signature changes
- No changes to `MemoryObject` schema or SQLite table structure
- Windows-compatible paths (use `pathlib.Path`)
- Python 3.10+

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `smos/memory/bm25_index.py` | **Create** | BM25Index class — build, update, query |
| `smos/memory/vector_store.py` | **Modify** | Instantiate BM25Index, call `update()` on every `store()` and `delete()` |
| `smos/compression/context_builder.py` | **Modify** | Run BM25 query, merge with FAISS results via RRF, replace fixed rerank formula |
| `tests/test_bm25_index.py` | **Create** | Unit tests for BM25Index build, update, query |
| `tests/bench_retrieval.py` | **Modify** | Add exact-term retrieval benchmark asserting BM25 hybrid beats vector-only |

---

## Background: Why BM25 + RRF

FAISS cosine similarity excels at semantic matching but degrades on exact terms — a query for `JWTValidator` returns memories about "authentication" because the embedding space clusters them semantically. BM25 (term frequency × inverse document frequency) excels at exact-term recall. Reciprocal Rank Fusion (RRF) merges two ranked lists without needing to calibrate score scales:

```
RRF_score(doc) = 1/(k + rank_in_vector_list) + 1/(k + rank_in_bm25_list)
```

where `k=60` (standard constant). Documents appearing in both lists score highest.

---

## Task 1 — BM25Index class

**Files:**
- Create: `smos/memory/bm25_index.py`
- Test: `tests/test_bm25_index.py`

**Interfaces:**
- Produces: `BM25Index` class with methods `build(corpus: list[tuple[str,str]])`, `update(uuid: str, content: str)`, `remove(uuid: str)`, `query(text: str, k: int) -> list[tuple[str, float]]`
- `corpus` is a list of `(uuid, content)` tuples

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bm25_index.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_bm25_index.py -v
```
Expected: `ModuleNotFoundError: No module named 'smos.memory.bm25_index'`

- [ ] **Step 3: Install rank-bm25**

```
pip install rank-bm25
```

Verify: `python -c "from rank_bm25 import BM25Okapi; print('ok')"` → prints `ok`

- [ ] **Step 4: Implement BM25Index**

Create `smos/memory/bm25_index.py`:

```python
from __future__ import annotations

import re
import threading
from typing import Optional

from rank_bm25 import BM25Okapi


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25Index:
    """In-memory BM25 index over stored memories. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._uuids: list[str] = []
        self._bm25: Optional[BM25Okapi] = None

    def build(self, corpus: list[tuple[str, str]]) -> None:
        """Build index from (uuid, content) pairs. Replaces existing index."""
        with self._lock:
            if not corpus:
                self._uuids = []
                self._bm25 = None
                return
            self._uuids = [uuid for uuid, _ in corpus]
            tokenized = [_tokenize(content) for _, content in corpus]
            self._bm25 = BM25Okapi(tokenized)

    def update(self, uuid: str, content: str) -> None:
        """Add a single document. Rebuilds the BM25 index."""
        with self._lock:
            if uuid in self._uuids:
                return
            tokens = _tokenize(content)
            if self._bm25 is None:
                self._uuids = [uuid]
                self._bm25 = BM25Okapi([tokens])
            else:
                existing_tokens = [
                    _tokenize(doc)
                    for doc in self._bm25.corpus_size * [""]  # placeholder
                ]
                # Rebuild: BM25Okapi has no incremental add; rebuild is cheap at <100K docs
                new_corpus = list(zip(self._uuids, [self._bm25.corpus[i] for i in range(len(self._uuids))]))
                new_corpus.append((uuid, tokens))
                self._uuids = [u for u, _ in new_corpus]
                self._bm25 = BM25Okapi([t for _, t in new_corpus])

    def remove(self, uuid: str) -> None:
        """Remove a document by uuid. Rebuilds index."""
        with self._lock:
            if uuid not in self._uuids:
                return
            idx = self._uuids.index(uuid)
            if self._bm25 is None:
                return
            new_corpus = [
                self._bm25.corpus[i]
                for i in range(len(self._uuids))
                if i != idx
            ]
            self._uuids = [u for u in self._uuids if u != uuid]
            self._bm25 = BM25Okapi(new_corpus) if new_corpus else None

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """Return top-k (uuid, score) pairs for the query text."""
        with self._lock:
            if self._bm25 is None or not self._uuids:
                return []
            tokens = _tokenize(text)
            scores = self._bm25.get_scores(tokens)
            ranked = sorted(
                zip(self._uuids, scores.tolist()),
                key=lambda x: x[1],
                reverse=True,
            )
            return [(uuid, float(score)) for uuid, score in ranked[:k] if score > 0.0]
```

**Note on `update()` — the corpus access pattern:** `BM25Okapi` stores tokenized corpus as `self._bm25.corpus`. The rebuild in `update()` uses it directly. This is an internal attribute of rank_bm25 but stable across versions.

- [ ] **Step 5: Run tests**

```
pytest tests/test_bm25_index.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 6: Commit**

```
git add smos/memory/bm25_index.py tests/test_bm25_index.py
git commit -m "feat: add BM25Index for in-memory term-frequency retrieval"
```

---

## Task 2 — Wire BM25Index into VectorStore

**Files:**
- Modify: `smos/memory/vector_store.py`

**Interfaces:**
- Consumes: `BM25Index` from `smos.memory.bm25_index`
- Produces: `VectorStore` gains a `.bm25` attribute of type `BM25Index`, seeded from SQLite on init; updated on every `store()` and `delete()`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_vector_store.py

def test_bm25_index_seeded_on_init(tmp_path):
    from smos.memory.vector_store import VectorStore
    from smos.memory.schemas import MemoryObject
    store = VectorStore(persist_path=tmp_path)
    mem = MemoryObject(type="doc", content="JWT token authentication middleware test")
    store.store(mem)

    # Reload from disk — BM25 must be rebuilt from SQLite
    store2 = VectorStore(persist_path=tmp_path)
    results = store2.bm25.query("JWT", k=5)
    assert len(results) > 0
    assert results[0][0] == mem.id


def test_bm25_updated_on_store(tmp_path):
    from smos.memory.vector_store import VectorStore
    from smos.memory.schemas import MemoryObject
    store = VectorStore(persist_path=tmp_path)
    mem = MemoryObject(type="doc", content="unique_function_name_xyz implementation")
    store.store(mem)
    results = store.bm25.query("unique_function_name_xyz", k=5)
    assert any(r[0] == mem.id for r in results)


def test_bm25_updated_on_delete(tmp_path):
    from smos.memory.vector_store import VectorStore
    from smos.memory.schemas import MemoryObject
    store = VectorStore(persist_path=tmp_path)
    mem = MemoryObject(type="doc", content="delete_me_token authentication test")
    mid = store.store(mem)
    store.delete(mid)
    results = store.bm25.query("delete_me_token", k=5)
    assert not any(r[0] == mid for r in results)
```

- [ ] **Step 2: Run to verify failure**

```
pytest tests/test_vector_store.py::test_bm25_index_seeded_on_init -v
```
Expected: `AttributeError: 'VectorStore' object has no attribute 'bm25'`

- [ ] **Step 3: Modify VectorStore**

In `smos/memory/vector_store.py`, add to imports:

```python
from smos.memory.bm25_index import BM25Index
```

In `VectorStore.__init__()`, after `self._index, self._db = self._init_db_and_index()`:

```python
self.bm25 = BM25Index()
self._seed_bm25()
```

Add new method:

```python
def _seed_bm25(self) -> None:
    """Build BM25 index from all memories in SQLite."""
    rows = self._db.execute(
        "SELECT uuid, content FROM memories ORDER BY row_id"
    ).fetchall()
    self.bm25.build([(r[0], r[1]) for r in rows])
```

In `store()` method, after the SQLite insert and FAISS add (still inside `_lock`):

```python
self.bm25.update(memory.id, memory.content)
```

In `delete()` method, after deleting from SQLite and FAISS:

```python
self.bm25.remove(uuid)
```

In `delete_batch()` method, after the batch delete loop:

```python
for uuid in uuids:
    self.bm25.remove(uuid)
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_vector_store.py -v
```
Expected: all tests PASS including the 3 new ones

- [ ] **Step 5: Commit**

```
git add smos/memory/vector_store.py tests/test_vector_store.py
git commit -m "feat: seed and maintain BM25 index in VectorStore"
```

---

## Task 3 — Hybrid Retrieval in context_builder.py

**Files:**
- Modify: `smos/compression/context_builder.py`

**Interfaces:**
- Consumes: `VectorStore.bm25: BM25Index` (from Task 2), `VectorStore.query()` (existing), `VectorStore.query_domain()` (existing)
- Produces: `build_compressed_context()` returns same `CompressedContext` type but with higher-quality candidates via RRF hybrid merge

**Reciprocal Rank Fusion formula:**

```
RRF(doc) = 1/(60 + rank_vector) + 1/(60 + rank_bm25)
```

Docs appearing only in one list get score `1/(60 + rank)` for that list and `0` for the other.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hybrid_retrieval.py
import pytest
from unittest.mock import MagicMock


def _make_store_with_memories(memories: list[dict]):
    """Helper: mock VectorStore for context builder tests."""
    from smos.memory.bm25_index import BM25Index
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories])
    store.bm25 = bm25
    store.get_by_uuid.side_effect = lambda uid: next(
        (m for m in memories if m["id"] == uid), None
    )
    return store


def test_exact_term_memory_ranks_high():
    """BM25 should boost exact-match memories above pure semantic matches."""
    from smos.compression.context_builder import build_compressed_context

    memories = [
        {"id": "id1", "content": "JWTValidator class handles token validation", "distance": 0.3, "tags": []},
        {"id": "id2", "content": "authentication is important for security", "distance": 0.1, "tags": []},
        {"id": "id3", "content": "the middleware pipeline processes requests", "distance": 0.25, "tags": []},
    ]
    store = _make_store_with_memories(memories)

    result = build_compressed_context("JWTValidator", k=2, store=store)
    assert result.confidence > 0.0
    # id1 has the exact term — should appear in sources
    assert "id1" in result.sources


def test_hybrid_returns_compressedcontext():
    from smos.compression.context_builder import build_compressed_context
    from smos.memory.schemas import CompressedContext

    memories = [
        {"id": "id1", "content": "test content for retrieval", "distance": 0.2, "tags": []},
    ]
    store = _make_store_with_memories(memories)
    result = build_compressed_context("test content", k=1, store=store)
    assert isinstance(result, CompressedContext)
```

- [ ] **Step 2: Run to verify failure**

```
pytest tests/test_hybrid_retrieval.py -v
```
Expected: FAIL (test infrastructure works but exact-term ranking assertion fails under current pipeline)

- [ ] **Step 3: Implement hybrid retrieval in context_builder.py**

Replace the existing `build_compressed_context` function body:

```python
_RRF_K = 60  # standard RRF constant


def _rrf_merge(
    vector_results: list[dict],
    bm25_results: list[tuple[str, float]],
    top_k: int,
) -> list[str]:
    """Merge two ranked lists via Reciprocal Rank Fusion. Returns ordered UUIDs."""
    scores: dict[str, float] = {}

    for rank, item in enumerate(vector_results):
        uid = item["id"]
        scores[uid] = scores.get(uid, 0.0) + 1.0 / (_RRF_K + rank + 1)

    for rank, (uid, _) in enumerate(bm25_results):
        scores[uid] = scores.get(uid, 0.0) + 1.0 / (_RRF_K + rank + 1)

    return sorted(scores, key=lambda u: scores[u], reverse=True)[:top_k]


def build_compressed_context(
    query: str,
    k: int,
    store: VectorStore,
    tags: Optional[list[str]] = None,
) -> CompressedContext:
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    # Vector retrieval
    domain_tags = tags or None
    vector_results = store.query_domain(query, k=candidate_k, domain_tags=domain_tags)
    if not vector_results:
        vector_results = store.query(query, k=candidate_k)

    # BM25 retrieval
    bm25_results = store.bm25.query(query, k=candidate_k)

    if not vector_results and not bm25_results:
        return CompressedContext(
            summary="No relevant memories found.",
            sources=[],
            confidence=0.0,
            mode="uncertain",
        )

    # Hybrid merge via RRF
    merged_uuids = _rrf_merge(vector_results, bm25_results, top_k=k * 2)

    # Hydrate merged results
    # Build a lookup from vector results first, then fill gaps from store
    vector_by_id = {r["id"]: r for r in vector_results}
    candidates = []
    for uid in merged_uuids:
        if uid in vector_by_id:
            candidates.append(vector_by_id[uid])
        else:
            record = store.get_by_uuid(uid)
            if record:
                candidates.append(record)

    # Final rerank: term overlap tiebreaker
    for c in candidates:
        c["_final_score"] = _term_overlap(query, c.get("content", ""))
    candidates.sort(key=lambda c: c.get("_final_score", 0.0), reverse=True)
    top = candidates[:k]

    memories_for_compression = [
        {"content": c.get("content", ""), "id": c.get("id", "")}
        for c in top
    ]
    sources = [c.get("id", "") for c in top]

    summary, mode = compress_memories_full(query, memories_for_compression)
    confidence = min(1.0, len(top) / max(k, 1))

    return CompressedContext(
        summary=summary,
        sources=sources,
        confidence=confidence,
        mode=mode,
    )
```

Also remove the now-dead `_classify_domain` function and `_rerank` function (replaced by `_rrf_merge`).

- [ ] **Step 4: Run tests**

```
pytest tests/test_hybrid_retrieval.py tests/test_bm25_index.py tests/test_vector_store.py -v
```
Expected: all PASS

- [ ] **Step 5: Run full benchmark suite**

```
pytest tests/bench_retrieval.py tests/bench_security.py tests/bench_mcp_tools.py tests/bench_performance.py tests/bench_compression.py -v
```
Expected: all benchmarks PASS at existing thresholds

- [ ] **Step 6: Commit**

```
git add smos/compression/context_builder.py tests/test_hybrid_retrieval.py
git commit -m "feat: hybrid BM25+FAISS retrieval via Reciprocal Rank Fusion"
```

---

## Task 4 — Exact-Term Retrieval Benchmark

**Files:**
- Modify: `tests/bench_retrieval.py`

**Goal:** Add a benchmark that proves BM25 hybrid beats vector-only for exact-term queries. This becomes a regression gate for future changes.

- [ ] **Step 1: Read the existing bench_retrieval.py structure**

```
cat tests/bench_retrieval.py
```
Understand existing fixtures, assertion patterns, and scoring thresholds before adding.

- [ ] **Step 2: Add the exact-term benchmark**

In `tests/bench_retrieval.py`, add:

```python
def test_exact_term_retrieval_quality(vector_store_with_corpus):
    """
    Hybrid retrieval must recall at least 80% of memories when queried
    by an exact function/class name that appears in their content.
    Regression gate: ensures BM25 is active and improving exact-term recall.
    """
    store = vector_store_with_corpus

    # Seed memories with exact identifiers
    exact_terms = [
        ("AuthMiddleware", "AuthMiddleware validates JWT tokens in the request pipeline"),
        ("DatabaseConnectionPool", "DatabaseConnectionPool manages postgres connections with retry logic"),
        ("RateLimiter", "RateLimiter enforces per-user request quotas using a sliding window"),
    ]
    ids = {}
    for term, content in exact_terms:
        from smos.memory.schemas import MemoryObject
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
        from smos.memory.schemas import MemoryObject
        store.store(MemoryObject(type="doc", content=n))

    from smos.compression.context_builder import build_compressed_context
    hits = 0
    for term, _ in exact_terms:
        result = build_compressed_context(term, k=3, store=store)
        if ids[term] in result.sources:
            hits += 1

    recall = hits / len(exact_terms)
    assert recall >= 0.80, f"Exact-term recall {recall:.2f} < 0.80 — BM25 may not be active"
```

- [ ] **Step 3: Run the benchmark**

```
pytest tests/bench_retrieval.py -v -k "exact_term"
```
Expected: PASS with recall >= 0.80

- [ ] **Step 4: Run full benchmark suite one final time**

```
pytest tests/ -v --tb=short
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```
git add tests/bench_retrieval.py
git commit -m "test: add exact-term retrieval benchmark as regression gate for BM25"
```

---

## Self-Review Checklist

- [x] `rank_bm25` install documented
- [x] BM25Index thread-safe (uses `threading.Lock`)
- [x] BM25 seeded from SQLite on VectorStore init (survives process restart)
- [x] BM25 updated on `store()`, `delete()`, `delete_batch()`
- [x] Dead code removed: `_classify_domain`, old `_rerank`
- [x] RRF constant `k=60` is the standard value (not a magic number)
- [x] No changes to MCP tool signatures
- [x] No changes to `MemoryObject` schema
- [x] No changes to SQLite table structure
- [x] Full benchmark suite runs in Task 3 Step 5 before final commit
- [x] Exact-term regression benchmark added in Task 4

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-30-bm25-hybrid-retrieval.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration. Use `superpowers:subagent-driven-development`.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Start with Task 1** — `BM25Index` class. It has zero dependencies on the rest of the system.

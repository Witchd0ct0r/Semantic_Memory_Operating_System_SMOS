# Phase 5.4 — Two-Stage Retrieval (Exact/Verbatim, Neighbor Expansion, Reconstruction) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SMOS the Stage 2 half of the two-stage retrieval loop (spec §7): a verbatim/exact recall strategy that returns unsummarized segments plus their ±1 ordinal neighbors for context, and a `reconstruct()` primitive that rebuilds a document's or section's exact original text from its stored segments — so an agent can go from Stage 1's lean compressed overview to Stage 2's precise, quotable source text.

**Architecture:** A new module, `smos/memory/exact_retrieval.py`, owns all Stage 2 logic — hybrid candidate retrieval (reusing `context_builder.py`'s private RRF-fusion helper rather than duplicating or modifying it), `±1` neighbor expansion by `ordinal`, and graph-driven `reconstruct()`. `recall_engine.py` gains one new dispatch branch (`strategy="exact"`) that delegates to it, keeping its existing per-strategy-helper shape unchanged. `server.py` gains one new MCP tool (`tool_reconstruct`); `tool_recall`'s existing `strategy` parameter already threads through to the new branch, so it only needs a docstring update. Along the way, this plan closes a standing tech-debt item: `VectorStore._query_internal()` only returned `tier` in its metadata dict (not `doc_id`/`ordinal`/`char_start`/`char_end`/`source_ids`/`confidence`, which `get_by_uuid()` already returns) — Stage 2 needs `doc_id`/`ordinal` on every hybrid-search hit to drive neighbor expansion, so this plan fixes the asymmetry at the source instead of re-hydrating every candidate via a second `get_by_uuid()` call.

**Tech Stack:** Python, SQLite (stdlib `sqlite3`), FAISS + BM25Okapi (existing), pytest. No new dependencies.

## Global Constraints

- **No new required runtime dependencies.**
- **Do not modify `smos/compression/context_builder.py`.** Per the design spec's explicit architecture decision (§7): "do not modify `context_builder.py` for Stage 2 — it is the benchmark-gated hybrid path." This plan only *imports* its existing private `_rrf_merge` helper — reusing a module's private helper across files without re-exporting it under a new public name is already an established pattern here (5.2/5.3 did the same with `segmenter.py`'s `_build_span_tree`/`_flatten_leaves`/`_merge_undersized`, and 5.3's plan documented this explicitly as intentional).
- **New module, not `recall_engine.py`, for the substantial Stage 2 logic.** The design spec's §10 impact table lists `recall_engine.py` as a candidate location, but §7's architecture decision explicitly allows "recall_engine.py (new strategies) **or a new module**." `recall_engine.py` today is a thin dispatcher plus three small one-off `_recall_*` helpers (77 lines total); `reconstruct()` doesn't fit that shape at all (no query/k, not a recall strategy, gets its own MCP tool), and it shares candidate-fetching machinery with the exact-recall strategy. Putting both in a dedicated `smos/memory/exact_retrieval.py` keeps `recall_engine.py`'s existing per-strategy-helper pattern intact and groups the two genuinely-related Stage 2 concerns together.
- **`_query_internal()` metadata fix is additive only.** New keys are added to the existing `metadata` dict; no key is removed or renamed. No test in the current suite asserts an exact key-set on `store.query()`/`store.query_domain()` results (verified by inspection — existing assertions check individual keys like `metadata["type"]`/`metadata["tags"]`), so this is safe.
- **`reconstruct(store, node_id)` takes one graph node id (document or section), not the design spec's illustrative `reconstruct(doc_id[, level])` two-parameter sketch.** A section is already an addressable graph node id (`_wire_containment` mints `f"{doc_id}:section:{i}"`), so "which level to reconstruct at" is fully expressed by *which node id you pass* — no separate depth/level parameter is needed. This is a pure interface simplification, not a scope or behavior change from the spec's intent ("reconstruction assembles at any level", §5).
- **Relies on, does not re-implement, existing guarantees.** Byte-exact, no-overlap segmentation (spec §5, shipped in 5.2/5.3) and the 3-level containment cap with its undirected-traversal caveat (`_wire_containment`'s docstring, `smos/ingest/segmenter.py`) are load-bearing preconditions for `reconstruct()` — this plan's tests prove composition, not those underlying guarantees.
- **Scope fence: this plan is sub-phase 5.4 only.** Do not implement the clarification-loop clustering signal (5.7 — needs Stage 1 cluster output, not built here), graph-propagation ranking (needs 5.5/5.6's populated graph — a no-op before then per spec §7), domain classification or traceability edges (5.5), or multilingual embeddings (5.8).
- **Test collection:** `pytest.ini` sets `python_files = ["test_*.py"]`. New tests go in `test_*.py` files under `tests/`.
- **Run tests:** `pytest tests/ -v --tb=short`. If you see an unexpected **"No tests collected"**, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken (known `rtk` hook quirk, see `docs/HANDOFF.md`).
- **Style:** `from __future__ import annotations` at the top of every new/modified module; builtin generic type hints (`list[str]`, `dict`, `Optional[...]`); no comments except where genuinely non-obvious (match the style already in every file this plan touches).

---

### Task 1: `VectorStore` — hybrid-query metadata parity + ordinal lookup

**Files:**
- Modify: `smos/memory/vector_store.py:273-320` (`_query_internal` — SELECT + result dict)
- Modify: `smos/memory/vector_store.py` (add `get_segment_by_ordinal`, right after `get_segment_ids_by_doc`, currently `smos/memory/vector_store.py:602-607`, before `get_checkpoint` at line 609)
- Test: `tests/test_vector_store_query_metadata.py`

**Interfaces:**
- Consumes: nothing new (existing `VectorStore`, `MemoryObject`).
- Produces: `VectorStore.query()`/`.query_domain()` results now include `source_ids`, `confidence`, `doc_id`, `ordinal`, `char_start`, `char_end` in each result's `metadata` dict (same shape `get_by_uuid()` already returns); `VectorStore.get_segment_by_ordinal(doc_id: str, ordinal: int) -> Optional[dict]` (same return shape as `get_by_uuid()`). Consumed by Task 2's `exact_retrieval.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vector_store_query_metadata.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_query_result_metadata_matches_get_by_uuid_shape(store: VectorStore) -> None:
    memory = MemoryObject(
        type="segment",
        content="Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1",
        ordinal=3,
        char_start=120,
        char_end=180,
    )
    memory_id = store.store(memory)

    results = store.query("explicit consent processing", k=1)
    assert results[0]["id"] == memory_id
    meta = results[0]["metadata"]
    assert meta["doc_id"] == "doc-1"
    assert meta["ordinal"] == 3
    assert meta["char_start"] == 120
    assert meta["char_end"] == 180
    assert meta["source_ids"] == []
    assert meta["confidence"] == 1.0


def test_query_domain_result_metadata_matches_get_by_uuid_shape(store: VectorStore) -> None:
    memory = MemoryObject(
        type="segment",
        content="Requirement REQ-42 governs password rotation cadence.",
        tags=["security"],
        doc_id="doc-2",
        ordinal=0,
        char_start=0,
        char_end=54,
    )
    store.store(memory)

    results = store.query_domain("password rotation", k=1, domain_tags=["security"])
    assert results[0]["metadata"]["doc_id"] == "doc-2"
    assert results[0]["metadata"]["ordinal"] == 0


def test_get_segment_by_ordinal_returns_matching_segment(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="segment", content="First segment body text here.", doc_id="doc-3", ordinal=0, char_start=0, char_end=30),
        MemoryObject(type="segment", content="Second segment body text here.", doc_id="doc-3", ordinal=1, char_start=30, char_end=61),
    ]
    ids = store.store_batch(memories)

    record = store.get_segment_by_ordinal("doc-3", 1)
    assert record is not None
    assert record["id"] == ids[1]
    assert record["metadata"]["ordinal"] == 1


def test_get_segment_by_ordinal_returns_none_when_absent(store: VectorStore) -> None:
    assert store.get_segment_by_ordinal("doc-does-not-exist", 0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vector_store_query_metadata.py -v --tb=short`
Expected: FAIL — the first two tests fail with `KeyError: 'doc_id'` (key not yet present in the metadata dict); the last two fail with `AttributeError: 'VectorStore' object has no attribute 'get_segment_by_ordinal'`.

- [ ] **Step 3: Extend `_query_internal` and add `get_segment_by_ordinal`**

In `smos/memory/vector_store.py`, replace the row-fetch and result-append block inside `_query_internal` (currently lines 294-319):

```python
                row = self._db.execute(
                    "SELECT uuid, type, content, timestamp, tags, tier "
                    "FROM memories WHERE row_id = ?",
                    (int(row_id),),
                ).fetchone()
                if row is None:
                    continue
                uuid_, type_, content, timestamp, tags, tier = row

                if domain_tags:
                    tag_set = {t.strip() for t in tags.split(",") if t.strip()}
                    if not tag_set.intersection(domain_tags):
                        continue

                results.append({
                    "id": uuid_,
                    "content": content,
                    "metadata": {
                        "type": type_,
                        "timestamp": timestamp,
                        "tags": tags,
                        "tier": tier,
                    },
                    "distance": float(1.0 - score),
                    "score": float(score),
                })
```

with:

```python
                row = self._db.execute(
                    "SELECT uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                    "doc_id, ordinal, char_start, char_end "
                    "FROM memories WHERE row_id = ?",
                    (int(row_id),),
                ).fetchone()
                if row is None:
                    continue
                (uuid_, type_, content, timestamp, tags, tier, source_ids, confidence,
                 doc_id, ordinal, char_start, char_end) = row

                if domain_tags:
                    tag_set = {t.strip() for t in tags.split(",") if t.strip()}
                    if not tag_set.intersection(domain_tags):
                        continue

                results.append({
                    "id": uuid_,
                    "content": content,
                    "metadata": {
                        "type": type_,
                        "timestamp": timestamp,
                        "tags": tags,
                        "tier": tier,
                        "source_ids": [s for s in source_ids.split(",") if s] if source_ids else [],
                        "confidence": confidence,
                        "doc_id": doc_id,
                        "ordinal": ordinal,
                        "char_start": char_start,
                        "char_end": char_end,
                    },
                    "distance": float(1.0 - score),
                    "score": float(score),
                })
```

Then add a new method right after `get_segment_ids_by_doc` (currently lines 602-607), before `get_checkpoint`:

```python
    def get_segment_by_ordinal(self, doc_id: str, ordinal: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT uuid FROM memories WHERE doc_id = ? AND ordinal = ?",
                (doc_id, ordinal),
            ).fetchone()
        if row is None:
            return None
        return self.get_by_uuid(row[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vector_store_query_metadata.py -v --tb=short`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all previously-passing tests still PASS (purely additive metadata keys; `get_segment_by_ordinal` is a new method, not a change to an existing signature).

- [ ] **Step 6: Commit**

```bash
git add smos/memory/vector_store.py tests/test_vector_store_query_metadata.py
git commit -m "fix: return full segment metadata from hybrid query results, add ordinal lookup"
```

---

### Task 2: `exact_retrieval.py` — candidate retrieval, neighbor expansion, `recall_exact`

**Files:**
- Create: `smos/memory/exact_retrieval.py`
- Test: `tests/test_exact_retrieval.py`

**Interfaces:**
- Consumes: `VectorStore.query`, `.get_by_uuid`, `.bm25.query`, `.get_segment_by_ordinal` (Task 1); `smos.compression.context_builder._rrf_merge` (existing, private, reused per the Global Constraints reuse pattern).
- Produces: `recall_exact(query_text: str, k: int, store: VectorStore) -> dict` (keys: `segments`, `context`, `sources`, `confidence`, `mode`); `expand_neighbors(store: VectorStore, segments: list[dict], window: int = 1) -> list[dict]`. Consumed by Task 4 (`recall_engine.py`) and Task 6 (end-to-end test).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_exact_retrieval.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.exact_retrieval import expand_neighbors, recall_exact
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_recall_exact_returns_verbatim_segments_no_compression(store: VectorStore) -> None:
    store.store(MemoryObject(
        type="segment", content="Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1", ordinal=0, char_start=0, char_end=58,
    ))
    result = recall_exact("explicit consent processing", 3, store)
    assert result["mode"] == "extractive"
    assert result["confidence"] == 1.0
    assert result["segments"]
    assert result["segments"][0]["content"] == "Article 9(2)(b) permits processing with explicit consent."
    assert result["sources"] == [result["segments"][0]["id"]]


def test_recall_exact_no_matches_returns_uncertain(store: VectorStore) -> None:
    result = recall_exact("anything at all", 3, store)
    assert result == {"segments": [], "context": [], "sources": [], "confidence": 0.0, "mode": "uncertain"}


def test_recall_exact_includes_neighbor_context(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="segment", content="Neighbor before the matching clause.", doc_id="doc-2", ordinal=0, char_start=0, char_end=37),
        MemoryObject(type="segment", content="Explicit consent processing is required here.", doc_id="doc-2", ordinal=1, char_start=37, char_end=84),
        MemoryObject(type="segment", content="Neighbor after the matching clause.", doc_id="doc-2", ordinal=2, char_start=84, char_end=120),
    ]
    store.store_batch(memories)

    result = recall_exact("explicit consent processing", 1, store)
    context_contents = {c["content"] for c in result["context"]}
    assert "Neighbor before the matching clause." in context_contents
    assert "Neighbor after the matching clause." in context_contents


def test_expand_neighbors_skips_segments_without_doc_id(store: VectorStore) -> None:
    record = {"id": "x", "metadata": {"doc_id": None, "ordinal": None}}
    assert expand_neighbors(store, [record]) == []


def test_expand_neighbors_deduplicates_against_input_segments(store: VectorStore) -> None:
    memories = [
        MemoryObject(type="segment", content="Segment zero body text here.", doc_id="doc-3", ordinal=0, char_start=0, char_end=29),
        MemoryObject(type="segment", content="Segment one body text here.", doc_id="doc-3", ordinal=1, char_start=29, char_end=57),
    ]
    ids = store.store_batch(memories)
    both = [store.get_by_uuid(i) for i in ids]

    neighbors = expand_neighbors(store, both)
    assert neighbors == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_exact_retrieval.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.memory.exact_retrieval'`

- [ ] **Step 3: Implement the module**

Create `smos/memory/exact_retrieval.py`:

```python
from __future__ import annotations

from smos.compression.context_builder import _rrf_merge
from smos.memory.vector_store import VectorStore

_CANDIDATE_MULTIPLIER = 4
_DEFAULT_NEIGHBOR_WINDOW = 1


def _fetch_ranked_candidates(query_text: str, k: int, store: VectorStore) -> list[dict]:
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    vector_results = store.query(query_text, k=candidate_k)
    bm25_results = [
        (uid, score) for uid, score in store.bm25.query(query_text, k=candidate_k) if score > 0.0
    ]
    if not vector_results and not bm25_results:
        return []

    merged = _rrf_merge(vector_results, bm25_results, top_k=k)

    vector_by_id = {r["id"]: r for r in vector_results}
    candidates: list[dict] = []
    for uid, _ in merged:
        if uid in vector_by_id:
            candidates.append(vector_by_id[uid])
        else:
            record = store.get_by_uuid(uid)
            if record is not None:
                candidates.append(record)
    return candidates


def expand_neighbors(
    store: VectorStore, segments: list[dict], window: int = _DEFAULT_NEIGHBOR_WINDOW
) -> list[dict]:
    """Pull up to `window` segments on each side of every input segment, by
    `ordinal` within the same `doc_id` — the replacement for RAG overlap
    (spec §5/§7): a matched segment brings its immediate context without
    duplicating stored text."""
    seen_ids = {s["id"] for s in segments if s.get("id")}
    neighbors: list[dict] = []
    for seg in segments:
        doc_id = seg.get("metadata", {}).get("doc_id")
        ordinal = seg.get("metadata", {}).get("ordinal")
        if doc_id is None or ordinal is None:
            continue
        for offset in range(1, window + 1):
            for neighbor_ordinal in (ordinal - offset, ordinal + offset):
                if neighbor_ordinal < 0:
                    continue
                neighbor = store.get_segment_by_ordinal(doc_id, neighbor_ordinal)
                if neighbor is None or neighbor["id"] in seen_ids:
                    continue
                neighbors.append(neighbor)
                seen_ids.add(neighbor["id"])
    return neighbors


def recall_exact(query_text: str, k: int, store: VectorStore) -> dict:
    """Stage 2 of the two-stage retrieval loop (spec §7): verbatim segments,
    no LLM compression, plus +/-1 neighbor context. Confidence is 1.0
    whenever at least one segment matches — the returned value is the
    record's exact stored text, so there is no summarization uncertainty
    to weigh."""
    candidates = _fetch_ranked_candidates(query_text, k, store)
    if not candidates:
        return {"segments": [], "context": [], "sources": [], "confidence": 0.0, "mode": "uncertain"}

    context = expand_neighbors(store, candidates)
    return {
        "segments": candidates,
        "context": context,
        "sources": [c["id"] for c in candidates],
        "confidence": 1.0,
        "mode": "extractive",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_exact_retrieval.py -v --tb=short`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/exact_retrieval.py tests/test_exact_retrieval.py
git commit -m "feat: add exact/verbatim retrieval with ordinal neighbor expansion"
```

---

### Task 3: `reconstruct()` — rebuild verbatim text from the containment tree

**Files:**
- Modify: `smos/memory/exact_retrieval.py` (append `reconstruct`)
- Test: `tests/test_reconstruct.py`

**Interfaces:**
- Consumes: `GraphStore.get_node`, `.traverse` (existing, `smos/memory/graph_store.py`); `VectorStore.get_by_uuid` (existing); `ingest_document_text`, `MarkdownAdapter` (existing, `smos/ingest/segmenter.py` / `smos/ingest/adapters/markdown.py` — test fixtures only).
- Produces: `reconstruct(store: VectorStore, node_id: str) -> dict` (keys: `node_id`, `text`, `segment_count`, `complete`, `error`). Consumed by Task 5 (`tool_reconstruct`) and Task 6 (end-to-end test).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reconstruct.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.segmenter import ingest_document_text
from smos.memory.exact_retrieval import reconstruct
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_reconstruct_unknown_node_returns_error(store: VectorStore) -> None:
    result = reconstruct(store, "does-not-exist")
    assert result["text"] == ""
    assert result["segment_count"] == 0
    assert result["complete"] is False
    assert result["error"]


def test_reconstruct_memory_node_returns_error(store: VectorStore) -> None:
    store.graph.add_node("mem-1", "memory", "mem-1")
    result = reconstruct(store, "mem-1")
    assert result["complete"] is False
    assert result["error"]


def test_reconstruct_document_concatenates_leaves_in_ordinal_order(store: VectorStore) -> None:
    text = "# Title\nIntro paragraph text goes here.\n## Section A\n" + ("Sentence about topic A. " * 60)
    result = ingest_document_text("doc-1", text, store, MarkdownAdapter())

    reconstructed = reconstruct(store, "doc-1")
    assert reconstructed["complete"] is True
    assert reconstructed["text"] == text
    assert reconstructed["segment_count"] == result["segment_count"]


def test_reconstruct_section_returns_only_its_own_leaves(store: VectorStore) -> None:
    text = "# Title\nBody.\n## Section A\n" + ("Sentence about A topic. " * 60)
    result = ingest_document_text("doc-2", text, store, MarkdownAdapter())
    assert result["section_count"] >= 1

    children = store.graph.traverse("doc-2", max_hops=1, edge_types=["contains"])
    section_ids = [n["id"] for n in children if n["type"] == "section"]
    assert section_ids

    section_result = reconstruct(store, section_ids[0])
    assert 0 < section_result["segment_count"] < result["segment_count"]
    assert section_result["text"] in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reconstruct.py -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'reconstruct' from 'smos.memory.exact_retrieval'`

- [ ] **Step 3: Implement `reconstruct`**

Append to `smos/memory/exact_retrieval.py`:

```python
def reconstruct(store: VectorStore, node_id: str) -> dict:
    """Rebuild the verbatim text under a `document` or `section` graph node
    (spec §7): fetch its segments, order by `ordinal`, concatenate. Because
    `GraphStore.traverse()` is undirected (see `_wire_containment`'s
    docstring in `smos/ingest/segmenter.py`), a section's 1-hop neighbors
    include its parent document alongside its leaf children — filtering to
    `type == "memory"` excludes the parent without needing a directed
    traversal. `complete` is False when the concatenated segments have a gap
    (their char offsets aren't contiguous) — e.g. a document whose
    containment tree isn't fully wired yet (still mid-checkpoint)."""
    node = store.graph.get_node(node_id)
    if node is None or node["type"] not in ("document", "section"):
        return {
            "node_id": node_id, "text": "", "segment_count": 0,
            "complete": False, "error": "node not found, or not a document/section node",
        }

    direct = store.graph.traverse(node_id, max_hops=1, edge_types=["contains"])
    segment_ids = [n["id"] for n in direct if n["type"] == "memory"]
    for section in (n for n in direct if n["type"] == "section"):
        section_children = store.graph.traverse(section["id"], max_hops=1, edge_types=["contains"])
        segment_ids.extend(n["id"] for n in section_children if n["type"] == "memory")

    records = [store.get_by_uuid(sid) for sid in segment_ids]
    records = [r for r in records if r is not None and r["metadata"].get("ordinal") is not None]
    records.sort(key=lambda r: r["metadata"]["ordinal"])

    text = "".join(r["content"] for r in records)
    complete = True
    prev_end = None
    for r in records:
        char_start = r["metadata"].get("char_start")
        if prev_end is not None and char_start != prev_end:
            complete = False
        prev_end = r["metadata"].get("char_end")

    return {
        "node_id": node_id,
        "text": text,
        "segment_count": len(records),
        "complete": complete,
        "error": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reconstruct.py -v --tb=short`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/memory/exact_retrieval.py tests/test_reconstruct.py
git commit -m "feat: add reconstruct() for byte-exact document/section rebuilding"
```

---

### Task 4: Wire `exact` strategy into `recall_engine.py`

**Files:**
- Modify: `smos/memory/recall_engine.py:1-37` (imports, `_VALID_STRATEGIES`, dispatch in `recall()`)
- Test: `tests/test_recall_engine.py` (append)

**Interfaces:**
- Consumes: `recall_exact` (Task 2, `smos/memory/exact_retrieval.py`).
- Produces: `recall(query, "exact", k, since, until, seed_id, component, store)` dispatch branch. `recall()`'s existing signature is unchanged. Consumed by Task 5 (`tool_recall` in `server.py` — already generic, needs only a docstring update).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recall_engine.py`:

```python
def test_recall_exact_returns_verbatim_segments(store: VectorStore) -> None:
    store.store(_mem(
        "Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1", ordinal=0, char_start=0, char_end=58,
    ))
    result = recall("explicit consent processing", "exact", 3, "", "", "", "", store)
    assert result["mode"] == "extractive"
    assert result["segments"]
    assert result["segments"][0]["content"] == "Article 9(2)(b) permits processing with explicit consent."


def test_recall_exact_no_matches_returns_uncertain(store: VectorStore) -> None:
    result = recall("nothing matches this", "exact", 3, "", "", "", "", store)
    assert result["sources"] == []
    assert result["mode"] == "uncertain"


def test_recall_exact_bumps_recall_count(store: VectorStore) -> None:
    mem_id = store.store(_mem(
        "Requirement REQ-42 governs password rotation cadence.",
        doc_id="doc-2", ordinal=0, char_start=0, char_end=54,
    ))
    recall("password rotation cadence", "exact", 3, "", "", "", "", store)
    row = store._db.execute(
        "SELECT recall_count FROM memories WHERE uuid = ?", (mem_id,)
    ).fetchone()
    assert row[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recall_engine.py -k exact -v --tb=short`
Expected: FAIL with `KeyError` (accessing `result["mode"]` on `{"error": "unknown strategy: exact"}`)

- [ ] **Step 3: Wire the dispatch branch**

In `smos/memory/recall_engine.py`, add an import right after the existing `from smos.memory.vector_store import VectorStore` line (currently line 8):

```python
from smos.memory.exact_retrieval import recall_exact
```

Change `_VALID_STRATEGIES` (currently line 10):

```python
_VALID_STRATEGIES = {"hybrid", "temporal", "chain", "repo", "exact"}
```

In `recall()`, change the dispatch chain (currently lines 27-34):

```python
    if strategy == "repo":
        result = _recall_repo(query, k, component, store)
    elif strategy == "temporal":
        result = _recall_temporal(query, k, since, until, store)
    elif strategy == "chain":
        result = _recall_chain(seed_id, k, store)
    else:
        result = build_compressed_context(query, k, store).model_dump()
```

to:

```python
    if strategy == "repo":
        result = _recall_repo(query, k, component, store)
    elif strategy == "temporal":
        result = _recall_temporal(query, k, since, until, store)
    elif strategy == "chain":
        result = _recall_chain(seed_id, k, store)
    elif strategy == "exact":
        result = recall_exact(query, k, store)
    else:
        result = build_compressed_context(query, k, store).model_dump()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_recall_engine.py -v --tb=short`
Expected: PASS (all tests, including 3 new ones)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/memory/recall_engine.py tests/test_recall_engine.py
git commit -m "feat: add exact strategy to the recall dispatcher"
```

---

### Task 5: `tool_reconstruct` MCP tool + `tool_recall` docstring update

**Files:**
- Modify: `smos/server.py:1-39` (imports)
- Modify: `smos/server.py:262-291` (`tool_recall` docstring)
- Modify: `smos/server.py` (insert `tool_reconstruct` immediately after `tool_recall`, before `tool_read_file_compress` at line 294)

**Interfaces:**
- Consumes: `reconstruct` (Task 3, `smos/memory/exact_retrieval.py`); `recall` (existing, already imported).
- Produces: `tool_reconstruct(node_id: str) -> dict` MCP tool, registered on `app`. Final task in this plan — nothing downstream consumes it within this sub-phase.

- [ ] **Step 1: Add the import**

In `smos/server.py`, add a new import right after `from smos.memory.recall_engine import recall` (currently line 11):

```python
from smos.memory.exact_retrieval import reconstruct
```

- [ ] **Step 2: Update `tool_recall`'s docstring**

In `smos/server.py`, replace `tool_recall`'s docstring (currently lines 272-287):

```python
    """Retrieve memories using an explicit strategy: hybrid, temporal, chain, or repo.

    hybrid (default): the existing vector+BM25+graph retrieval pipeline.
    temporal: filter by [since, until] ISO-8601 timestamps, then rank by similarity.
    chain: follow evolves_into/related_to edges from seed_id; k is hop depth here, not result count.
    repo: hybrid retrieval scoped to a single component tag.

    Args:
        query: Natural language query (unused for the chain strategy).
        strategy: One of hybrid, temporal, chain, repo (default "hybrid").
        k: Result count for hybrid/temporal/repo, or hop depth for chain (default 5).
        since: ISO-8601 lower timestamp bound for temporal (empty = unbounded).
        until: ISO-8601 upper timestamp bound for temporal (empty = unbounded).
        seed_id: Starting memory id for the chain strategy.
        component: Tag to scope the repo strategy to.
    """
```

with:

```python
    """Retrieve memories using an explicit strategy: hybrid, temporal, chain, repo, or exact.

    hybrid (default): the existing vector+BM25+graph retrieval pipeline.
    temporal: filter by [since, until] ISO-8601 timestamps, then rank by similarity.
    chain: follow evolves_into/related_to edges from seed_id; k is hop depth here, not result count.
    repo: hybrid retrieval scoped to a single component tag.
    exact: verbatim segments (no LLM compression) plus +/-1 ordinal neighbor context —
           Stage 2 of the two-stage retrieval loop. Pair with tool_reconstruct to rebuild
           the full surrounding document or section from the matched segments.

    Args:
        query: Natural language query (unused for the chain strategy).
        strategy: One of hybrid, temporal, chain, repo, exact (default "hybrid").
        k: Result count for hybrid/temporal/repo/exact, or hop depth for chain (default 5).
        since: ISO-8601 lower timestamp bound for temporal (empty = unbounded).
        until: ISO-8601 upper timestamp bound for temporal (empty = unbounded).
        seed_id: Starting memory id for the chain strategy.
        component: Tag to scope the repo strategy to.
    """
```

- [ ] **Step 3: Add the `tool_reconstruct` tool**

In `smos/server.py`, insert this new tool immediately after `tool_recall`'s closing (currently ending line 291), before `tool_read_file_compress` (currently line 294):

```python
@app.tool()
def tool_reconstruct(node_id: str) -> dict:
    """Reconstruct the verbatim text of a document or section from its stored segments.

    Fetches every segment under `node_id` (a document or section graph node id —
    e.g. the "doc_id" returned by tool_ingest_document, or a section id such as
    "<doc_id>:section:0"), orders them by ordinal, and concatenates their exact
    stored text. Pair with tool_recall(strategy="exact") for the two-stage
    retrieval loop: exact finds the matching segments, reconstruct rebuilds
    their surrounding document.

    Returns {"node_id", "text", "segment_count", "complete", "error"}.
    `complete` is False when the segments have a gap (e.g. an in-progress
    ingest, or char offsets aren't contiguous) — the text may not be fully
    byte-exact in that case.

    Args:
        node_id: A document or section graph node id.
    """
    try:
        return reconstruct(_store, node_id)
    except Exception as exc:
        return {
            "node_id": node_id, "text": "", "segment_count": 0,
            "complete": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
```

- [ ] **Step 4: Verify the module imports cleanly**

Run: `python -c "import smos.server"`
Expected: no output, exit code 0 — confirms no syntax errors or missing names. (Per the established precedent from Phase 5.3: `@app.tool()` wrappers aren't unit-tested directly here, since importing `smos.server` instantiates a real `VectorStore` against `cwd()/.smos`. All real logic is already covered by Tasks 2-4's tests against `recall_exact`/`reconstruct`/`recall` directly.)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/server.py
git commit -m "feat: register tool_reconstruct MCP tool, document exact strategy"
```

---

### Task 6: End-to-end two-stage retrieval proof

**Files:**
- Test: `tests/test_two_stage_retrieval_e2e.py`

**Interfaces:**
- Consumes: `markdown_to_payload` (Phase 5.3, `smos/ingest/transformers/markdown_transformer.py`); `do_ingest_document_schema` (Phase 5.3, `smos/tools/ingest_tools.py`); `recall` (Task 4, `smos/memory/recall_engine.py`); `reconstruct` (Task 3, `smos/memory/exact_retrieval.py`).
- Produces: nothing new — this task adds no production code; it proves Tasks 1-5 compose correctly against a real ingested document.

- [ ] **Step 1: Write the test**

Create `tests/test_two_stage_retrieval_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.exact_retrieval import reconstruct
from smos.memory.recall_engine import recall
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_two_stage_flow_exact_recall_then_reconstruct_document(store: VectorStore) -> None:
    md = (
        "# Title\nIntro paragraph about contract law basics.\n"
        "## Section A\n" + ("Provisions about liability under contract law. " * 40) + "\n"
        "## Section B\nA short closing clause about termination rights.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-e2e")
    ingest_result = do_ingest_document_schema(payload, store)
    assert ingest_result["status"] == "success"
    doc_id = ingest_result["doc_id"]

    stage2 = recall("termination rights", "exact", 3, "", "", "", "", store)
    assert stage2["mode"] == "extractive"
    assert stage2["segments"]
    assert any("termination" in s["content"].lower() for s in stage2["segments"])

    reconstructed = reconstruct(store, doc_id)
    assert reconstructed["complete"] is True
    assert reconstructed["text"] == md
    assert reconstructed["segment_count"] == ingest_result["segment_count"]


def test_reconstruct_section_returns_strict_subset_of_document(store: VectorStore) -> None:
    md = "# Title\nBody.\n## Section A\n" + ("Sentence about A topic. " * 60) + "\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-section")
    ingest_result = do_ingest_document_schema(payload, store)
    assert ingest_result["section_count"] >= 1

    children = store.graph.traverse(ingest_result["doc_id"], max_hops=1, edge_types=["contains"])
    section_ids = [n["id"] for n in children if n["type"] == "section"]
    assert section_ids

    section_result = reconstruct(store, section_ids[0])
    assert 0 < section_result["segment_count"] < ingest_result["segment_count"]
    assert section_result["text"] in md
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_two_stage_retrieval_e2e.py -v --tb=short`
Expected: PASS (2 tests) — no new production code in this task; it proves Tasks 1-5 compose correctly against a real ingested document.

- [ ] **Step 3: Run the full suite to confirm the whole sub-phase is green**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS — this is the final task in the plan.

- [ ] **Step 4: Commit**

```bash
git add tests/test_two_stage_retrieval_e2e.py
git commit -m "test: prove exact recall + reconstruct compose end-to-end over a real ingest"
```

---

## Post-plan: HANDOFF update

Not a task with tests, but required before ending the session: update `docs/HANDOFF.md`'s "Where things stand" section to record Phase 5.4 completion (commit range, test count delta, any deviations from this plan discovered during implementation), and update the sub-phase table to mark 5.4 **DONE** and point at the next chosen track (5.5, or Phase 4/Track B, per the Governor's call).

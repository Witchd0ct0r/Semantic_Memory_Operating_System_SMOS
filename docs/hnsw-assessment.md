# HNSW assessment (2026-10-29) — for a later session

**Recommendation: do not migrate to HNSW yet.** Exact search is not the bottleneck at the corpus sizes SMOS will see in a
trial, HNSW cannot delete vectors in place (SMOS deletes constantly), and three cheaper costs hit first. Revisit when a real
store passes roughly 300,000 segments or a measured query p95 on the target hardware exceeds the budget.

## What exists today

- `VectorStore` holds `faiss.IndexIDMap(IndexFlatIP(384))`: exact inner-product search over L2-normalised vectors, row ids
  from SQLite (`memories.row_id`), file `faiss.index` rewritten by `_save_index()`.
- `_rebuild_index_if_stale()` re-embeds every row when the index and SQLite counts differ (the recovery path).
- Delete paths: `delete()`, `delete_batch()` (used by every re-ingest through `delete_document_segments`), and the lifecycle
  dedup. All call `remove_ids`.

## Measured on the dev PC (FAISS 1.14.3, 384 dimensions, top-20, single query, CPU)

| Segments | Flat (exact) | HNSW (M=32, efSearch=64) | HNSW build | Index memory |
|---|---|---|---|---|
| 10,000 | 1.4 ms | 0.5 ms | 0.9 s | 15 MB flat / ~18 MB HNSW |
| 100,000 | 4.5 ms | 1.1 ms | 34 s | 154 MB / ~179 MB |
| 300,000 | 12.8 ms | 1.0 ms | 129 s | 461 MB / ~538 MB |

Probe: random unit vectors, so the **recall column was left out on purpose** (random data has no cluster structure; recall
numbers from it say nothing about real text). A real decision needs the same table on embeddings of the actual corpus:
build both indexes from the same matrix, time 200 queries, compare the HNSW top-10 with the flat top-10.

Reading: the flat index answers in ~13 ms at 300,000 segments on a laptop. The older figures in `ARCHITECTURE.md` section 4
("100-1000 ms at 5K-50K", "ceiling 10K-20K on CPU-only hardware") are far more pessimistic than this measurement and should be
re-measured on the Debian host before anyone relies on them. A requirements/standards corpus for a company trial is more likely
tens of thousands of segments than hundreds of thousands.

## Why HNSW is not a drop-in

1. **No deletion.** `IndexHNSWFlat.remove_ids` and `IndexIDMap2(IndexHNSWFlat)` both raise in this FAISS build (confirmed).
   SMOS re-ingests a changed document by deleting and re-adding all its segments, so deletion is the normal path, not an edge
   case. Options: (a) soft-delete (filter hits through SQLite, keep a tombstone set) plus a periodic full rebuild, which at
   129 s per 300,000 vectors must run off the request path and swap atomically; (b) rebuild per re-ingest (too slow);
   (c) a second, small flat index for recent vectors merged with the HNSW result.
2. **Approximate results.** SMOS's selling point is exact-text retrieval; the vector side feeds RRF fusion with BM25, so a recall
   loss would silently drop candidates. It needs a recall target (e.g. recall@50 >= 0.98 against flat on the real corpus) and
   `efSearch` tuning, and the `requirement_context` candidate pools (`max(k*100, 1000)`) ask for large k, which is where HNSW
   recall degrades most.
3. **Filtering.** Domain-tag filtering happens after the vector search (post-filter). With HNSW the pool must be oversized
   further to keep enough hits after filtering.
4. **Persistence and recovery.** HNSW files are larger and slower to write, and `_save_index()` rewrites the whole file on every
   delete today. The recovery path (`_rebuild_index_if_stale`) would take minutes instead of seconds at scale.
5. **Update/rollback interplay.** The self-updater snapshots the whole data dir; a bigger `faiss.index` makes snapshots slower
   and larger.

## Costs that hit before search latency does

Already recorded as technical debt; they matter more than search time up to several hundred thousand segments:

- `BM25Index.update()/remove()` rebuild the whole index per call (single `store()` / `delete()`), inside `VectorStore._lock`.
  `add_batch`/`remove_batch` amortise this, which is why batched ingest is fine and one-off writes are not.
- `embed()` runs while holding `VectorStore._lock`, so a slow embedding blocks every reader.
- `delete()` rewrites `faiss.index` on every call.
- Lifecycle dedup is O(M x n).

## Suggested order if growth ever demands it

1. Re-measure on the Debian host with the real corpus (latency p95, recall of any approximate index).
2. Fix `embed()`-outside-lock and incremental BM25 first (no behaviour change, pure throughput).
3. Only then introduce an approximate index behind a small `VectorIndex` seam inside `VectorStore`, with: soft-delete +
   background rebuild, a recall regression test against the flat index on a fixed corpus, a config switch (`flat` stays the
   default), and a rebuild path that reuses `_rebuild_index_if_stale`. IVF-flat is the alternative if memory, not latency, is the
   limit (it supports `remove_ids` but needs training data).

## Measured update (2026-10-29)

The admin **Benchmark** page now measures exactly the comparison this document asks for (HNSW built from the store's own vectors
over generated requirements text, recall@10 and recall@50 against exact search, per-stage query time, projection to a latency budget)
on the installed device. A first Quick run on the dev PC (up to 5,157 segments) gave HNSW recall@10 of 1.000, exact search of 0.04-0.38
ms against HNSW 0.04-0.10 ms, and showed BM25 (1.8 -> 21.5 ms) rather than vector search (2-3 ms) as the part of a query that grows.
That supports "not yet": see `docs/benchmark-verification.md` for the numbers and what is still to be measured on the Debian host.

## Decision needed from the Governor

None now. Ask again if a store passes ~300,000 segments, Debian query p95 exceeds an agreed budget, or ingest throughput (not
search) becomes the complaint, in which case the items under "Costs that hit before search latency does" come first.

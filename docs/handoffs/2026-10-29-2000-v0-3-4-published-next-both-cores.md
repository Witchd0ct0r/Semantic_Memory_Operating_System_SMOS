# Handoff — v0.3.4 published and measured on Debian; next: use both cores (0.3.5) — 2026-10-29 2000

## State

Release **v0.3.4 is pushed, tagged and published** (`main` clean and in sync with `origin/main`; 1078 tests pass). Debian was updated and
benchmarked by the Governor: one Quick and four Standard runs on 0.3.4 (two with the default torch threads, two with
`SMOS_TORCH_THREADS=1`). All completed with cleanup verified and **zero request errors**, so the occasional HTTP 500 and the mixed-test
writer errors are gone on the target host too. Keyword search is now cheap (the BM25 stage is no longer significant) and the
whole query plus throughput are several times better than 0.3.3; the 200 ms budget is comfortably met at the largest measured size. The
company-host figures are company data and are **not** in the repository (memory `feedback-no-benchmark-data-in-repo`); this handoff states
conclusions only. No server or background process started by the assistant is running (checked).

## Completed this session

- Calibrated the benchmark comparison flags per metric (`smos/bench/analysis.py`, `NOISY_FLAGS` 25/40, `STEADY_FLAGS` 10/20), commit `133bd1c`.
- Designed, planned and built release 0.3.4 test-first: numpy inverted-index BM25 with a read-write lock (`smos/memory/bm25_index.py`,
  `rwlock.py`; the old class is kept as the oracle `tests/bm25_reference.py`), `SMOS_TORCH_THREADS`, benchmark error analytics
  (`smos/bench/errorlog.py`, "Request errors" report section). Spec `docs/superpowers/specs/2026-10-29-bm25-index-design.md`, plan
  `docs/superpowers/plans/2026-10-29-bm25-index.md`.
- Codex review 8 (`docs/superpowers/review/2026-10-29-8-review-package.md`, `...-8-review.md`): 1 Important + 3 Minor, all fixed; resolution
  in `docs/benchmark-verification.md` section 8. The Important one was a **pre-existing defect the new error analytics exposed**: one SQLite
  connection used under two locks (`VectorStore`, `GraphStore`) gave intermittent 500s during ingest; now one shared `RLock`
  (`tests/test_store_connection_sharing.py`).
- Debian measurement conclusions (qualitative): queries are far faster; **throughput is still flat from 1 to 64 clients and server CPU stays
  near one core**, so the second core is unused. `SMOS_TORCH_THREADS=1` vs the default: queries equal within noise, slightly lower CPU, reads
  suffer less while a document is being ingested, but the benchmark's own ingest is clearly slower (batch embedding likes two threads). The
  default stays unset. Query p95 at the largest size varied too much between identical-code runs to flag on; p50 and throughput varied less.

## Next steps (in order)

1. **Comparison metric change (small, test-first):** in `smos/bench/analysis.py` compare on query **p50** (flags 25/40) and demote p95 to
   informational (or loosen it to 50/80); update `tests/test_bench_analysis.py` and the provisional-calibration note in
   `docs/benchmark-verification.md` (no figures). Needs the Governor's yes (proposed, not yet approved).
2. **Brainstorm + design 0.3.5 "use both cores"** (the Governor asked what can be done about embedding time, which is ~60% of the search
   stages but only ~30% of a whole request). Proposed scope, approval pending:
   - a small cache of recent query embeddings (agents repeat queries; measure the repeat rate from the usage log first);
   - move `embed()` out of `VectorStore._lock` in `_query_internal` (`smos/memory/vector_store.py`) so two queries embed in parallel
     (pair with `SMOS_TORCH_THREADS=1`); expect roughly +50%, not 2x, because the rest of a request is still serialized;
   - **profile one loaded request on Debian** (py-spy or cProfile) to explain the ~20 ms per request that is not search stages (suspects: HTTP
     handling, usage-log write, `bump_recall` commit per query);
   - three things grow with store size and look like full scans or missing indexes (unverified): graph expansion, document text fetch
     (`/nodes/{id}/text`), small-document ingest through the API. Check `EXPLAIN QUERY PLAN` on `get_segment_ids_by_doc`, `GraphStore` edge
     lookups; a new index needs a numbered store migration (`smos/storage/migrations.py`, never edit an old one).
   - only if the 60-client target is still missed afterwards: batch concurrent query embeddings into one forward pass; ONNX/int8 (needs a
     rebuilt image, so not dashboard-updatable).
3. Release as **0.3.5 or higher** (never retag 0.3.4); follow `docs/RELEASING.md`. After release the Governor reruns Quick and Standard twice
   on Debian and sends the JSON files (compare, do not commit the figures).

## Open items / blockers

- Targets from the Governor: trial 10-15 simultaneous clients, production 60+, ingestion rare/off hours from fewer than 10 parsers; wait
  1-2 s acceptable for a person, longer for agents. After 0.3.4 the trial target is met for people; 60+ clients is acceptable for agents only.
- Still pending from earlier sessions: the two Debian checks (`docs/guide-verification.md` section 4); read the guide as a newcomer; delete
  `C:\Private\smos-update-drill`; unpublish the old PyPI project.
- Note: `docs/handoffs/2026-10-29-1700-next-dashboard-page-and-benchmarking.md` sorts before this file but is **stale** (its two goals, a new
  dashboard page and automated benchmarking, were delivered as the Benchmark page); `/boot` should read this file.
- Deferred minors: the Benchmark page can hang up to 30 s while a start waits for the API drain; a stored NaN would make the JSON download
  fail; the dev-PC (Windows) is the only place the new code ran besides Debian.

## Key decisions & context

- Execution style unchanged: brainstorm, written design, plan, inline TDD, own review, Codex package (report only), no reviewer subagents.
- SMOS_TORCH_THREADS stays unset by default (measured trade-off, not a lever). The real limiter is the shared lock plus the interpreter lock.
- BM25 contract: exact `BM25Okapi` scores/order; `build()` now keeps the first row of a repeated uuid; `rank-bm25` stays in `pyproject.toml`
  (tests use it; removing it would force a rebuilt image).
- Files that force a rebuilt image (`pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/`, `smos/launcher.py`) were not touched.
- Python heredocs in bash mangle backslashes and quotes; write patch scripts with the Write tool and run them (see `docs/runbooks/benchmark.md`
  gotchas). Test command: `rtk proxy python -m pytest tests -q` (about 3.5 minutes).
- Reusable operational recipes: `docs/runbooks/benchmark.md` (including reading the Errors section and comparing `SMOS_TORCH_THREADS`),
  `docs/runbooks/debian-deploy.md`.

## Files that matter

- `docs/HANDOFF.md` — current-state summary (updated this session).
- `smos/memory/vector_store.py` — `_lock` (now `RLock`, shared with `GraphStore`), `_query_internal` (embeds under the lock), `bump_recall`.
- `smos/memory/embeddings.py` — `embed`, `embed_batch`, `apply_threads`, `configured_threads`.
- `smos/bench/analysis.py` — comparison flags to change in step 1.
- `docs/benchmark-verification.md` — verification record, sections 5 and 8.
- `docs/superpowers/review/2026-10-29-8-review.md` — the Codex review and its fixes.

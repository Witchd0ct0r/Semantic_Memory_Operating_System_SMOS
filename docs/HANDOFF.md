# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-16)

**Phase 1 — BM25 Hybrid Retrieval: COMPLETE**, merged to `main` at commit `b890a19`.

- Added `smos/memory/bm25_index.py` — `BM25Index` (thread-safe, in-memory BM25Okapi over
  the SQLite corpus).
- Modified `smos/memory/vector_store.py` — wired `self.bm25` into `store()`/`delete()`
  and their batch variants.
- Modified `smos/compression/context_builder.py` — replaced the fixed 70/30
  FAISS/term-overlap rerank with Reciprocal Rank Fusion (`_rrf_merge`) over FAISS +
  BM25 ranked lists; removed dead `_classify_domain`/`_rerank`.
- New dependency: `rank-bm25>=0.2.2`.
- Test suite grew 86 → 103. New exact-term recall gate
  (`tests/test_exact_term_recall.py`) shows hybrid 1.00 vs vector-only 0.33 recall.
- Follow-up whole-branch-review fix (also in `b890a19`): batched `BM25Index`
  add/remove for `store_batch()`/`delete_batch()` (previously only `store()`/`delete()`
  got the O(n)-rebuild-avoidance treatment), made RRF score the primary sort key with
  term-overlap only as a tiebreaker (it had been silently overriding RRF), and filtered
  zero-score BM25 padding out of fusion input.

**Known limitation carried into Phase 2:** `BM25Index.update()`/`.remove()` (the
single-item paths) still do a full `BM25Okapi` rebuild per call — O(n) per call, O(n²)
cumulative at scale, held inside `VectorStore._lock`. Batch paths (`add_batch`/
`remove_batch`) amortize this to once per call. Accepted as a documented Phase-1
limitation; needs an incremental-index redesign before Phase 4 (HNSW) if corpora grow
large.

**Housekeeping (this session, 2026-07-16):**
- Added project `CLAUDE.md` (repo root) — project description, this handoff pointer,
  SMOS-tool-usage policy, and the governor/orchestrator/Haiku-implementation workflow.
- Retired `docs/superpowers/plans/HANDOFF-bm25-next-session.md` as historical-only
  (superseded banner added, pointing here).
- Corrected `ARCHITECTURE.md` sections that predated Phase 1: read-path diagram, the
  "domain classification is dead code" issue (resolved), and the strengths bullet
  describing the old 70/30 rerank (now RRF).
- Removed a stray empty directory (`c:Privatesemantic_memorydocssuperpowersplans/`) —
  a mangled path artifact, not user content.
- `docs/` was untracked in git before this session; still untracked — not committed
  automatically, ask the governor before committing.

## Next up — Phase 2: Memory Graph (est. 2–3 sessions)

Plan: `docs/superpowers/plans/2026-06-30-smos-roadmap.md`, "Phase 2" section.

SQLite `graph_nodes`/`graph_edges` tables, an entity extractor (Python AST + regex),
a graph-expansion step in `context_builder.py`, and a new `tool_graph_query` MCP tool.
Moves SMOS from "retrieves similar text" to "retrieves connected knowledge" (e.g.
`auth` → memories → entities → `JWT` → `middleware` → `auth_service` via traversal).

Before writing literal code into a plan doc for this phase: read the *current*
signatures of anything the plan snippet touches first. Phase 1's plan drifted from the
real tree (nonexistent methods/attributes, wrong arg order) — see
`bm25-phase1-lessons` in cross-session memory for specifics. Treat plan code blocks as
drafts to verify, not transcripts to copy.

## Standing technical debt (address opportunistically, not yet scheduled)

- `embed()` called while holding `VectorStore._lock` — blocks all concurrent
  store/query/delete while an embedding computes.
- N individual SQLite `SELECT`s per query result — no `WHERE uuid IN (...)` batching.
- Tier labels (hot/warm/cold) stored but never used to filter FAISS search or drive
  eviction — cosmetic only; scheduled for Phase 3.
- `compress_memories_full` has no input-size guard before the LLM call — large
  candidate sets can silently exceed the model's context window.

## Test commands

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts, not `def test_*`
suites. Don't point a regression gate at one and expect `pytest` to run it.

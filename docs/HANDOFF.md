# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-16)

**Phase 3 — Agent Memory Primitives: COMPLETE**, merged to `main` at commit `dedc3d5`
(feature branch `phase3-agent-primitives`, 17 commits, merged with `--no-ff`).

- Added `smos/tools/agent_primitives.py` — 7 primitive functions: `remember`/`learn`
  (thin `store()` wrappers differing only in `type`/`confidence` defaults), `forget`
  (wraps existing `delete()`), `link` (validates both ids are graph nodes + valid
  `edge_type` before `graph.add_edge()`), `snapshot` (defaults path to project root,
  wraps `do_semantic_snapshot_repo`), `compress` (non-destructive: LLM-merges N
  memories via the existing `compress_memories_full`, sources are **kept**, linked via
  `source_ids` + `generated_from` graph edges), `reflect` (LLM reasons over retrieved
  memories via a new, distinct prompt — `reflect_on_memories` in `summarizer.py` — and
  stores the result back as a durable `type="reflection"` memory).
- Added `smos/memory/recall_engine.py` — `recall(query, strategy, k, since, until,
  seed_id, component, store)`: `hybrid` (default) and `repo` delegate to the existing,
  **unmodified** `build_compressed_context()`; `temporal` filters by SQL timestamp
  range then vector-reranks; `chain` follows `evolves_into`/`related_to` graph edges
  from a seed memory (`k` means hop depth for this strategy only). `context_builder.py`
  was deliberately never touched this phase, to keep the Phase 1/2 benchmark-gated
  retrieval path stable.
- Data model: `MemoryObject` gained `source_ids: list[str]` and `confidence: float`
  (both default to no-ops for pre-existing rows); `memories` table gained
  `source_ids`/`confidence`/`recall_count`/`last_recalled` columns via the same
  `ALTER TABLE ADD COLUMN` pattern already used for `tier`.
- Tier promotion fixed: `LifecycleManager._promote_tiers()` now calls
  `VectorStore.recompute_tiers_by_frequency()` (single SQL `CASE WHEN` on
  `recall_count`/`last_recalled`) instead of insertion-order positional slicing.
  `hot` = recalled ≤7d or `recall_count>10`; `warm` = ≤30d or `recall_count>3`; else
  `cold`. **Computation only** — tiers still don't filter search or drive eviction
  (deliberately out of scope this phase; see tech debt below).
- Every retrieval path now feeds tier tracking: `tool_semantic_query`,
  `tool_graph_query`, and `recall()` all call the new `VectorStore.bump_recall()`.
- Registered 8 new MCP tools in `server.py`: `tool_remember`, `tool_forget`,
  `tool_learn`, `tool_link`, `tool_compress`, `tool_reflect`, `tool_snapshot`,
  `tool_recall` — all follow the existing try/except-and-stringify-error convention.
- Added `tests/bench_phase3_primitives.py` (standalone, non-pytest-collected).
- Test suite grew 142 → 187, all passing on merged `main`.

**Pre-Phase-1 vs current (Phase 1+2+3) benchmark (`tests/bench_full_evolution.py`,
commit `dedc3d5`, run 2026-07-16):** reproduces the pre-Phase-1 baseline (commit
`23f051c`) on the current tree by monkeypatching BM25/graph off, same no-drift
rationale as `bench_phase2_vs_phase1.py`, rather than checking out the old commit.
- **Exact-term recall** (3 identifiers, 180 noise docs — what BM25 fixed):
  pre-Phase-1 **1/3 (33%)** → now **3/3 (100%)**.
- **Graph-rescue recall** (5 scenarios, target reachable only via a shared entity —
  what the graph fixed): pre-Phase-1 **0/5 (0%)** → now **5/5 (100%)**.
- **MCP tool surface**: pre-Phase-1 **10 tools** → now **19 tools** (+9, +90%).

**Known limitation, accepted for this phase:** no tier-based search filtering or
eviction (tier is computed correctly now but has no behavioral consequence yet); no
retroactive backfill of `source_ids`/`confidence`/recall-tracking for memories stored
before this phase (same posture as Phase 2's graph backfill gap — new rows only).

**Execution notes for future phases:** all 9 tasks were dispatched to Haiku
implementer subagents via `superpowers:subagent-driven-development`, working in a
shared git worktree (`.worktrees/phase3-agent-primitives`) sequentially. Every task's
diff and the final whole-branch review were reviewed **inline by the orchestrator**
(standing preference — see `feedback-inline-reviews` in cross-session memory) — no
separate reviewer subagents were dispatched. All 17 commits matched the plan's literal
code verbatim; no fix-subagent dispatches were needed this phase. One environment
quirk worth carrying forward: this machine's `pytest` invocation goes through an `rtk`
proxy hook that occasionally mis-reports **"No tests collected"** on a run that
actually executed hundreds of tests correctly — confirmed by rerunning the identical
command via `rtk proxy pytest ...` (bypasses the filtering) and getting the real
pass count. **Don't trust a bare "No tests collected" result on this machine** — retry
through `rtk proxy` before concluding anything is broken.

One pre-existing, non-blocking test-design flake surfaced during Task 8 review:
`tests/test_recall_engine.py::test_recall_hybrid_matches_build_compressed_context`
asserts byte-identical output from two separate live LLM calls (temperature=0.1, not
0), so it can occasionally fail on wording drift between calls even though both paths
are correct. Passed clean on rerun with no code change. Worth a follow-up to assert
structural equality (same `sources`, non-empty `summary`) instead of exact string
equality — not scheduled yet.

## Prior phases

- **Phase 2 — Memory Graph** (merged `fa3ed64`, 2026-07-16): SQLite graph layer
  (`graph_nodes`/`graph_edges`), entity extraction (AST + backtick regex), additive
  graph-expansion step in retrieval, `tool_graph_query`. Graph-rescue recall 0%→100%
  vs Phase 1 baseline; write-path latency +11.0% (12.28ms→13.62ms/store).
- **Phase 1 — BM25 Hybrid Retrieval** (merged `b890a19`, 2026-07-02): RRF fusion of
  FAISS + BM25Okapi in `context_builder.py`; exact-term recall gate 0.33→1.00.

## Next up — Phase 4: HNSW Migration (est. 1 session)

Plan pointer: `docs/superpowers/plans/2026-06-30-smos-roadmap.md`, "Phase 4" section
(high-level only — write a detailed, tree-verified plan before executing, same
discipline as Phases 1-3: read current signatures from the actual files, not from this
handoff or the roadmap sketch).

Replace `faiss.IndexFlatIP` with `faiss.IndexHNSWFlat` for O(log n) approximate
search. Key open question the plan must resolve: HNSW doesn't support `remove_ids` —
either soft-delete in SQLite + periodic rebuild, or wrap in `IndexIDMap2`. Target:
query latency < 10ms at 100K vectors (currently ~35ms with `IndexFlatIP`). Consider
folding in the `BM25Index.update()`/`.remove()` full-rebuild tech debt below at the
same time, since both are "needs an incremental-index redesign at scale" problems.

## Standing technical debt (address opportunistically, not yet scheduled)

- `embed()` called while holding `VectorStore._lock` — blocks all concurrent
  store/query/delete while an embedding computes.
- N individual SQLite `SELECT`s per query result — no `WHERE uuid IN (...)` batching.
- Tier labels (hot/warm/cold) are now computed correctly (Phase 3) but still not used
  to filter FAISS search or drive eviction — cosmetic only.
- `compress_memories_full` has no input-size guard before the LLM call — large
  candidate sets can silently exceed the model's context window. `reflect_on_memories`
  (Phase 3) shares this gap since it follows the same pattern.
- `BM25Index.update()`/`.remove()` single-item full-rebuild cost — O(n) per call,
  O(n²) cumulative at scale, held inside `VectorStore._lock`; batch paths amortize
  this. Needs an incremental-index redesign before Phase 4 if corpora grow large.
- `GraphStore` has no corpus backfill for pre-existing memories — same "needs a
  redesign if it matters at scale" shape as the BM25 item.
- `tests/test_recall_engine.py::test_recall_hybrid_matches_build_compressed_context`
  is flaky (see Phase 3 section above) — asserts exact LLM-output equality across two
  live calls instead of structural equality.

## Test commands

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts, not `def test_*`
suites. Don't point a regression gate at one and expect `pytest` to run it.

On this machine, if a `pytest` run reports **"No tests collected"** unexpectedly,
retry with `rtk proxy pytest tests/ -q --tb=short` before assuming something broke —
see the Phase 3 execution notes above.

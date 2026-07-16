# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-16)

**Phase 2 — Memory Graph: COMPLETE**, merged to `main` at commit `fa3ed64`
(feature branch `phase2-memory-graph`, 9 commits, merged with `--no-ff`).

- Added `smos/memory/graph_store.py` — `GraphStore`: SQLite `graph_nodes`/`graph_edges`
  tables (own connection sharing, `CREATE TABLE IF NOT EXISTS`), node/edge CRUD, and a
  bounded recursive-CTE `traverse()` (undirected BFS, terminates on `hop < max_hops`
  regardless of cycles).
- Added `smos/memory/entity_extractor.py` — `extract_entities()`: Python `ast` walk
  (functions/classes/import targets) + backtick-span regex (works on any content,
  matches this project's own memory-file convention). Deduped, capped at 50/call.
- Added `GraphNode`/`GraphEdge` Pydantic schemas (`smos/memory/schemas.py`) — `type`/
  `edge_type` `Literal` enums kept in sync with `GraphStore`'s SQL `CHECK` constraints
  (verified in the final whole-branch review).
- Wired into `VectorStore` (`smos/memory/vector_store.py`): every `store()`/
  `store_batch()` call extracts entities and adds a `memory` node + `entity` nodes +
  `references` edges; `delete()`/`delete_batch()` remove the memory node and its edges
  (entity nodes are left in place — they're shared vocabulary, not owned by one memory).
- Added an **additive** graph-expansion step in `context_builder.py`: after the
  existing RRF-ranked top-`k` is computed, up to 2 graph-linked memories the ranking
  missed are appended (never reordering/removing what RRF already chose). Confirmed
  via the Phase 1 exact-term recall gate (`tests/test_exact_term_recall.py`) that this
  is a true no-op on non-code, backtick-free content — both its tests still pass.
- Added `tool_graph_query` MCP tool (`smos/server.py` + `smos/tools/semantic_tools.py`)
  for explicit "what connects to X" traversal queries.
- Added `tests/bench_graph_retrieval.py` (standalone, non-pytest-collected, matches the
  existing `bench_*.py` convention).
- Test suite grew 103 → 142, all passing on merged `main`.

**Known limitation, accepted for this phase:** the graph is populated only by new
`store()`/`store_batch()` calls going forward — it does **not** retroactively parse
memories already in an existing `.smos/` corpus (unlike BM25's `_seed_bm25()`, which
does rebuild from all of SQLite on init). A full-corpus graph backfill is out of scope;
track as a future opportunistic task if a corpus needs it.

**Execution notes for future phases:** all 7 tasks were dispatched to Haiku
implementer subagents via `superpowers:subagent-driven-development`, working in a
shared git worktree (`.worktrees/phase2-memory-graph`) sequentially — each task built
on the previous task's commit. Every task and the final whole-branch review were
reviewed **inline by the orchestrator**, not via separate reviewer subagents (standing
preference — see `feedback-inline-reviews` in cross-session memory). One real
mistake this session: the first implementer dispatch used `isolation: "worktree"` on
the `Agent` tool, which silently created a *second*, disconnected worktree branched
from stale `origin/main` instead of using the shared worktree named in the prompt —
had to cherry-pick that commit onto the correct branch and clean up the orphan.
**Never set `isolation` on `Agent` calls when coordinating sequential tasks in a
pre-existing shared worktree** — only the "Work from: `<path>`" instruction in the
prompt controls that, and `isolation: "worktree"` overrides it silently.

**Housekeeping carried from the prior session (2026-07-16, same day):** project
`CLAUDE.md` added (SMOS-tool-usage policy + governor/orchestrator/Haiku workflow),
`ARCHITECTURE.md` corrected for post-Phase-1 drift, stale
`HANDOFF-bm25-next-session.md` retired with a superseded banner, `docs/` brought under
version control.

## Prior phase — BM25 Hybrid Retrieval (COMPLETE, merged `b890a19`, 2026-07-02)

RRF fusion of FAISS + BM25Okapi in `context_builder.py`; new `BM25Index` class;
exact-term recall gate went from 0.33 (vector-only) to 1.00 (hybrid). Known limitation
carried forward: `BM25Index.update()`/`.remove()` (single-item paths) still do a full
rebuild per call — O(n) per call, O(n²) cumulative at scale, held inside
`VectorStore._lock`; batch paths amortize this. Needs an incremental-index redesign
before Phase 4 (HNSW) if corpora grow large.

## Next up — Phase 3: Agent Memory Primitives (est. 2 sessions)

Plan pointer: `docs/superpowers/plans/2026-06-30-smos-roadmap.md`, "Phase 3" section
(high-level only — write a detailed, tree-verified plan before executing, same as
Phases 1 and 2).

`remember`/`forget`/`compress`/`reflect`/`learn`/`link`/`snapshot`/`recall` MCP tools.
Frequency-based tier promotion (replacing the current insertion-order-based promotion
in `LifecycleManager`). Temporal + memory-chain retrieval modes. This phase is a
natural place to also address the "tiers stored but never used to filter/evict"
technical debt below, since tier semantics are central to it.

Before writing literal code into that plan: read the *current* signatures of
everything the plan snippet touches, in the actual files, at plan-writing time — not
from this handoff, not from memory, not from the roadmap sketch. Both Phase 1 and
Phase 2's plans needed this discipline; Phase 2 explicitly reused Phase 1's own tests/
files/fixtures as ground truth rather than trusting the roadmap doc, and that caught
what would otherwise have been silent drift.

## Standing technical debt (address opportunistically, not yet scheduled)

- `embed()` called while holding `VectorStore._lock` — blocks all concurrent
  store/query/delete while an embedding computes.
- N individual SQLite `SELECT`s per query result — no `WHERE uuid IN (...)` batching.
- Tier labels (hot/warm/cold) stored but never used to filter FAISS search or drive
  eviction — cosmetic only; scheduled for Phase 3.
- `compress_memories_full` has no input-size guard before the LLM call — large
  candidate sets can silently exceed the model's context window.
- `BM25Index.update()`/`.remove()` single-item full-rebuild cost (see Phase 1 section
  above) — needs resolving before Phase 4 (HNSW) if corpora grow large.
- `GraphStore` has no corpus backfill for pre-existing memories (see Phase 2 section
  above) — same "needs a redesign if it matters at scale" shape as the BM25 item.

## Test commands

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts, not `def test_*`
suites. Don't point a regression gate at one and expect `pytest` to run it.

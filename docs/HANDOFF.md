# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-21)

**Phase 5.1 — Foundation: COMPLETE**, merged to `main` at commit `363a553`
(feature branch `worktree-phase5-foundation`, 9 commits, fast-forward merge).

This is the first slice of Phase 5 (Domain-General Ingestion & Structure-Aware
Retrieval) — the design spec's §11 open decisions (~13 of them) were resolved with the
Governor this session (see `docs/superpowers/specs/2026-07-17-domain-general-ingestion-and-retrieval-design.md`,
§11 and the 2026-07-21 revision-log entry, for the full resolution list), then a
tree-verified implementation plan was written and executed for the foundational slice
only (`docs/superpowers/plans/2026-07-21-phase5-foundation-schema-and-adapters.md`).

- Added a `document` node type to `GraphNode`/`GraphStore` (additive CHECK-constraint
  widening, with a one-time legacy-schema migration since SQLite can't `ALTER` a CHECK
  constraint in place — detects the old constraint via `sqlite_master.sql` and
  rebuilds the table, preserving every row).
- Added `smos/ingest/adapters/` — a pluggable `DomainAdapter` Protocol
  (`structural_boundaries`, `extract_references`, `llm_boundary_prompt`), a registry
  (`register_adapter`/`get_adapter`/`all_edge_types`/`reset_registry`) that unions a
  fixed core edge-vocabulary with whatever each registered adapter contributes, and one
  minimal concrete adapter (`MarkdownAdapter`, splits on ATX headings) proving the
  interface end-to-end.
- Opened `GraphEdge.edge_type` from a fixed Pydantic `Literal` / SQL `CHECK` to free
  `TEXT` — validity is now enforced in application code (`agent_primitives.link()`)
  against the adapter registry's `all_edge_types()`, not the schema or the DB. Same
  legacy-migration pattern as the node-type change.
- Added `doc_id`/`ordinal`/`char_start`/`char_end` columns to `MemoryObject` and the
  `memories` table (same `ALTER TABLE ADD COLUMN` pattern as Phase 3's `source_ids`/
  `confidence`) — reconstruction metadata for ingested document segments, consumed by
  later sub-phases (5.2 segmentation, 5.4 retrieval/reconstruction).
- `LifecycleManager._deduplicate()` now excludes `type="segment"` memories — prevents
  the existing similarity-based dedup from incorrectly deleting legitimately-distinct
  near-identical clauses/requirements once documents start being ingested as segments.
- Test suite grew 187 → 207, all passing on merged `main`.

**Execution notes:** all 5 tasks dispatched to Haiku implementer subagents via
`superpowers:subagent-driven-development`, working in a worktree created via the
native `EnterWorktree` tool. Every task's diff and the final whole-branch review were
reviewed **inline by the orchestrator** (standing preference — see
`feedback-inline-reviews` in cross-session memory); no separate reviewer subagents
were dispatched. All 5 implementers matched their task briefs verbatim — no fix-agent
dispatches were needed. One environment gotcha worth flagging: `EnterWorktree`'s
default `fresh` base-ref branches from `origin/<default-branch>`, and this repo's
`origin` remote is 47 commits behind local `main` (local work was never pushed) — the
worktree initially had none of Phases 1–3. Caught before dispatching Task 1 by
diffing `smos/memory/` against the expected file list; fixed with
`git reset --hard main` inside the worktree. **Check this before trusting a
freshly-created worktree on this repo** — `git log --oneline -3` should show recent
Phase commits, not the pre-Phase-1 baseline.

## Prior phases

- **Phase 3 — Agent Memory Primitives** (merged `dedc3d5`, 2026-07-16): 7 agent
  primitives (`remember`/`learn`/`forget`/`link`/`snapshot`/`compress`/`reflect`),
  `recall_engine.py` (hybrid/temporal/chain/repo strategies), tier-promotion fix,
  8 new MCP tools (19 total). Pre-Phase-1 vs Phase 1+2+3 benchmark: exact-term recall
  33%→100%, graph-rescue recall 0%→100%.
- **Phase 2 — Memory Graph** (merged `fa3ed64`, 2026-07-16): SQLite graph layer
  (`graph_nodes`/`graph_edges`), entity extraction (AST + backtick regex), additive
  graph-expansion step in retrieval, `tool_graph_query`. Graph-rescue recall 0%→100%
  vs Phase 1 baseline; write-path latency +11.0% (12.28ms→13.62ms/store).
- **Phase 1 — BM25 Hybrid Retrieval** (merged `b890a19`, 2026-07-02): RRF fusion of
  FAISS + BM25Okapi in `context_builder.py`; exact-term recall gate 0.33→1.00.

## Next up — two independent tracks available

**Track A — Phase 5.2: Segmentation cascade (Pillar B).** Natural continuation of
5.1. Builds `smos/ingest/segmenter.py`: structural → semantic → size-fit → LLM
cascade, no overlap (neighbor expansion replaces it — see Pillar D), constructs the
containment tree (`document` → `section` → `paragraph`, wired via `contains` edges
using the `document` node type and adapter interface from 5.1). Depends only on 5.1
(shipped). See the sub-phase breakdown below for 5.3 onward.

**Track B — Phase 4: HNSW Migration (est. 1 session).** Independent of Phase 5;
pure search performance. Plan pointer: `docs/superpowers/plans/2026-06-30-smos-roadmap.md`,
"Phase 4" section (high-level only — write a detailed, tree-verified plan before
executing). Replace `faiss.IndexFlatIP` with `faiss.IndexHNSWFlat` for O(log n)
approximate search. Key open question the plan must resolve: HNSW doesn't support
`remove_ids` — either soft-delete in SQLite + periodic rebuild, or wrap in
`IndexIDMap2`. Target: query latency < 10ms at 100K vectors (currently ~35ms with
`IndexFlatIP`). Consider folding in the `BM25Index.update()`/`.remove()` full-rebuild
tech debt at the same time.

**Sequencing note:** the multilingual-embeddings open decision (spec §9) was resolved
this session as an **optional, lazy-loaded extra** (not a hard dependency swap) — so
it no longer forces Phase 5 work to land before Phase 4. Track A and Track B can now
proceed in either order; pick based on which is more valuable next, not on a
dimensionality-change constraint.

## Also queued — Phase 5, sub-phases 5.3 onward

**Design spec (all §11 open decisions resolved 2026-07-21):**
`docs/superpowers/specs/2026-07-17-domain-general-ingestion-and-retrieval-design.md`
**Diagrams:** `docs/superpowers/specs/diagrams/` — `ingestion-pipeline.svg`,
`two-stage-retrieval.svg`, `multi-domain-evaluation.svg`.

**Goal.** Let an agent answer hard questions over large ingested corpora (legal,
requirements, standards — any domain) with a two-stage retrieval loop: lean overview →
iterative clarification → exact verbatim provisions, at far leaner context than raw
document search. Driving use cases: "consequences of X under Hungarian law", and
"what do we need to verify this requirement" (multi-domain systems engineering).

**Sub-phase breakdown** (agreed with the Governor 2026-07-21 — each is an
independently plannable, tree-verified, testable slice, mirroring how Phases 1–3
shipped):

| Sub-phase | Content | Depends on | Status |
|---|---|---|---|
| **5.1** | Foundation: `document` node type, open edge vocab, segment metadata columns, adapter interface + minimal adapter, dedup-scoping exclusion | — | **DONE** (`363a553`) |
| **5.2** | Segmentation cascade (structural → semantic → size-fit → LLM), containment tree | 5.1 | next |
| **5.3** | Ingestion contract (Pydantic schema, quarantine validation) + `ingest_tools.py` integration, resumable-checkpoint large-payload path | 5.1, 5.2 | queued |
| **5.4** | Two-stage retrieval: exact/verbatim strategy, ±1 neighbor expansion, `reconstruct()` | 5.1 | queued |
| **5.5** | Multi-domain traceability & fan-out (Pillar F): domain classification, typed edges, evidence-gathering primitives | 5.1, 5.4 | queued |
| **5.6** | Graph-propagation ranking fold (PageRank+RRF) | 5.5 (needs a populated graph) | queued |
| **5.7** | Clarification-loop cluster signal (Pillar E) | 5.4 | queued |
| **5.8** | Multilingual embeddings (optional extra, cross-cutting) | can land anytime before Phase 4 | queued |

**Where the work lands in the current tree** (spec §3 and §10 have the full detail).
Two stages remain domain-coupled and biased toward *code repos* until 5.3 lands:
`smos/tools/ingest_tools.py` (code-repo ingester, one lossy summary per file) and
`smos/memory/entity_extractor.py` (Python-AST + backtick-spans only, extracts nothing
from prose — why the Phase 2 graph is inert on documents; see
`tests/bench_legal_regulations.py`'s docstring). The retrieval engine itself
(`context_builder.py`) is already domain-neutral and stays as-is.

**Next actions for a fresh session on this track:**
1. Read `docs/superpowers/plans/2026-07-21-phase5-foundation-schema-and-adapters.md`
   for the exact pattern 5.1 used (task structure, migration approach, TDD steps) —
   5.2's plan should follow the same discipline.
2. Write a detailed, **tree-verified** implementation plan for 5.2 — read current
   signatures from the actual files, not from this handoff or the spec (see the
   Phase 1 plan-drift lesson in `bm25-phase1-lessons`).
3. Slice into self-contained Haiku-dispatchable tasks per the CLAUDE.md workflow.

## Standing technical debt (address opportunistically, not yet scheduled)

- `embed()` called while holding `VectorStore._lock` — blocks all concurrent
  store/query/delete while an embedding computes.
- N individual SQLite `SELECT`s per query result — no `WHERE uuid IN (...)` batching.
- Tier labels (hot/warm/cold) are computed correctly (Phase 3) but still not used
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
  is flaky — asserts exact LLM-output equality across two live calls instead of
  structural equality.
- `_query_internal()` in `vector_store.py` still only returns `tier` in its metadata
  dict (not `source_ids`/`confidence`/`doc_id`/`ordinal`/`char_start`/`char_end`,
  all of which `get_by_uuid()` does return) — a pre-existing asymmetry, left alone
  again in Phase 5.1 since wiring it into the hybrid retrieval path is Phase 5.4's
  concern (two-stage retrieval), not this slice's.

## Test commands

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts, not `def test_*`
suites. Don't point a regression gate at one and expect `pytest` to run it.

On this machine, if a `pytest` run reports **"No tests collected"** unexpectedly,
retry with `rtk proxy pytest tests/ -q --tb=short` before assuming something broke.

# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-25)

**Phase 5.4 — Two-stage retrieval: IN PROGRESS (4 of 6 tasks complete, reviewed clean).**
Being built via subagent-driven-development in an **uncommitted-to-main worktree**:

- **Worktree path:** `.claude/worktrees/phase5-4-two-stage-retrieval`
- **Branch:** `worktree-phase5-4-two-stage-retrieval` (based on `main` @ `2763b26`)
- **Plan:** `docs/superpowers/plans/2026-07-25-phase5-4-two-stage-retrieval.md`
  (committed on the worktree branch as `a392f78`, not yet on `main`)
- **Ledger (resume point):** `.superpowers/sdd/2026-07-25-phase5-4-two-stage-retrieval/progress.md`
  inside the worktree (gitignored — only exists in that worktree's filesystem, not in git
  history; don't delete the worktree until the plan finishes or the ledger is no longer needed)
- **main is untouched** — still at `2763b26`, still 265 tests passing. All new work
  (commits `dd43875`, `dc72f2c`, `9eedb50`, `d677f66`, plus the plan-doc commit `a392f78`)
  lives only on the worktree branch, which has 281 passing tests.

**Done so far (Tasks 1-4 of 6, each reviewed inline by the orchestrator — clean, no
open findings):**
1. `dd43875` — Fixed the long-standing `_query_internal()` metadata asymmetry (see the
   tech-debt item below, now resolved on this branch): `.query()`/`.query_domain()`
   results carry full segment metadata (`doc_id`/`ordinal`/`char_start`/`char_end`/
   `source_ids`/`confidence`), matching what `get_by_uuid()` already returned. Added
   `VectorStore.get_segment_by_ordinal(doc_id, ordinal)`.
2. `dc72f2c` — New module `smos/memory/exact_retrieval.py`: `recall_exact()` (Stage 2 —
   verbatim segments, no LLM compression) and `expand_neighbors()` (±1 ordinal
   neighbor context, the no-overlap segmentation's replacement for RAG chunk overlap).
   Reuses `context_builder.py`'s private `_rrf_merge` by import, per the design spec's
   explicit "don't modify context_builder.py for Stage 2" architecture decision.
3. `9eedb50` — `reconstruct(store, node_id)` appended to `exact_retrieval.py`: rebuilds
   byte-exact text under a `document` or `section` graph node by walking `contains`
   edges and concatenating segments in `ordinal` order.
4. `d677f66` — Wired `"exact"` into `recall_engine.py`'s strategy dispatch.

**Remaining (per the plan):**
5. `tool_reconstruct` MCP tool + `tool_recall` docstring update (`smos/server.py`).
6. End-to-end proof test (`tests/test_two_stage_retrieval_e2e.py`) — ingest via 5.3's
   `markdown_to_payload`/`do_ingest_document_schema`, run `recall("...", "exact", ...)`,
   then `reconstruct()`, assert byte-exact round-trip.
Then the plan's final whole-branch review and merge.

**Two process incidents this session (both resolved, both worth knowing before
dispatching more implementers on this branch):**
- **Task 1's implementer committed directly to `main`** in the primary checkout
  instead of the worktree — its `Bash` tool's `cd` apparently didn't stick the way
  expected, or it just didn't run the check. Diagnosed via `git log`/`git status` in
  both locations; the commit was clean (touched only the 2 intended files), so it was
  cherry-picked onto the worktree branch (`dd43875`) and `main` was `git reset --hard`
  back to `2763b26` — done with explicit user confirmation since resetting `main` is
  destructive, even though local-only. **Fix applied to every dispatch from Task 2
  onward:** the implementer prompt now opens with a mandatory `git branch --show-current`
  check that must print the worktree branch name before touching anything, with an
  explicit warning about this exact prior incident. Tasks 2-4 committed correctly.
- **Task 4's implementer's `Edit` tool still leaked the same change into `main`'s
  *working tree*** as uncommitted (never committed) modifications, even though its
  `Bash`-driven git commit correctly landed on the worktree branch — `cd` only affects
  a Bash tool's own shell state, not where `Edit`/`Write` resolve paths. Caught by
  checking `git status` in the main checkout after every task, not just its commit
  log; discarded via `git checkout -- <files>` (safe — content was already committed
  on the worktree branch). **Lesson for whoever resumes this plan:** after every task,
  check `git status --short` in the **primary checkout** (`C:\private\semantic_memory`,
  not just the worktree) — a clean worktree commit does not guarantee the main
  checkout wasn't also touched.

**Next action for a fresh session resuming this plan:** `cd` into the worktree
(`.claude/worktrees/phase5-4-two-stage-retrieval`), read the ledger at
`.superpowers/sdd/2026-07-25-phase5-4-two-stage-retrieval/progress.md`, and dispatch
Task 5 (`tool-brief` script already knows how, see the plan file and the
`subagent-driven-development` skill). Both process incidents above are already
recorded in the ledger with full detail.

## Prior phases

- **Phase 5.3 — Ingestion contract + `ingest_tools.py` integration** (merged `a18fa4b`,
  2026-07-25): versioned Pydantic ingestion schema + quarantine validation
  (`smos/ingest/contract.py`), resumable checkpointed orchestrator
  (`do_ingest_document_schema`), `tool_ingest_document` MCP tool, markdown reference
  transformer. Test suite 227 → 265. Full narrative in this file's git history
  (`git log -p -- docs/HANDOFF.md`) if needed — compressed here now that 5.4 supersedes
  it as the active thread.
- **Phase 5.2 — Segmentation cascade** (merged `5f42dd7`, 2026-07-21): structural →
  semantic → size-fit cascade (`smos/ingest/segmenter.py`), `section` node type,
  three-level containment tree (`document` → `section` → segment),
  byte-exact-reconstruction test. Test suite 207 → 227.
- **Phase 5.1 — Foundation** (merged `363a553`, 2026-07-21): `document` node type,
  open adapter-extensible edge vocabulary, segment metadata columns
  (`doc_id`/`ordinal`/`char_start`/`char_end`), `DomainAdapter` interface +
  `MarkdownAdapter`, dedup-scoping exclusion for `type="segment"` memories. Test
  suite 187 → 207.
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

## Next up

**Immediate: finish Phase 5.4** (in progress — see "Where things stand" above).
Resume the worktree, dispatch Task 5, then Task 6, then the plan's final
whole-branch review and merge.

**After 5.4 merges, two independent tracks are available:**

**Track A' — Phase 5.5+:** multi-domain traceability & fan-out (Pillar F), per the
sub-phase table below.

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
as an **optional, lazy-loaded extra** (not a hard dependency swap) — so it no longer
forces Phase 5 work to land before Phase 4. Once 5.4 merges, Track A' and Track B can
proceed in either order; pick based on which is more valuable next.

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
| **5.2** | Segmentation cascade (structural → semantic → size-fit), containment tree (`section` node type, `ingest_document_text`) | 5.1 | **DONE** (`5f42dd7`) |
| **5.3** | Ingestion contract (Pydantic schema, quarantine validation) + `ingest_tools.py` integration, resumable-checkpoint large-payload path | 5.1, 5.2 | **DONE** (`a18fa4b`) |
| **5.4** | Two-stage retrieval: exact/verbatim strategy, ±1 neighbor expansion, `reconstruct()` | 5.1 | **IN PROGRESS** (4/6 tasks, worktree `worktree-phase5-4-two-stage-retrieval`) |
| **5.5** | Multi-domain traceability & fan-out (Pillar F): domain classification, typed edges, evidence-gathering primitives | 5.1, 5.4 | queued |
| **5.6** | Graph-propagation ranking fold (PageRank+RRF) | 5.5 (needs a populated graph) | queued |
| **5.7** | Clarification-loop cluster signal (Pillar E) | 5.4 | queued |
| **5.8** | Multilingual embeddings (optional extra, cross-cutting) | can land anytime before Phase 4 | queued |

**Where the work lands in the current tree** (spec §3 and §10 have the full detail).
`smos/tools/ingest_tools.py` now has two ingestion paths: the original code-repo
ingester (lossy per-file summary, unchanged) and the new schema-driven
`do_ingest_document_schema()` path (structure-preserving, byte-exact). Domain
generality still has one gap: `smos/memory/entity_extractor.py` remains
Python-AST + backtick-spans only, extracting nothing from prose — the Phase 2 graph
stays inert on ingested documents until an adapter's `extract_references()` is
implemented (5.5's job; see `tests/bench_legal_regulations.py`'s docstring). The
retrieval engine itself (`context_builder.py`) is already domain-neutral and stays
as-is.

**5.4's plan is already written and 4/6 tasks are done** — see "Where things stand"
above for the resume point; this isn't a fresh-planning task anymore.

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
  dict on `main` (not `source_ids`/`confidence`/`doc_id`/`ordinal`/`char_start`/
  `char_end`, all of which `get_by_uuid()` does return) — **fixed on the in-progress
  Phase 5.4 worktree branch (`worktree-phase5-4-two-stage-retrieval`, commit
  `dd43875`), not yet merged to `main`.** Remove this bullet once 5.4 merges.
- `GraphStore.traverse()`'s undirected BFS means a `contains` query from a `section`
  node returns its parent `document` alongside its real children — any 5.4+
  reconstruction/retrieval code walking the containment tree must filter by node
  type (see `_wire_containment`'s docstring in `smos/ingest/segmenter.py`). Not a
  bug in `traverse()` itself (its bidirectionality is correct for Phase 2/3's
  entity-relation use case) — just a caveat containment-tree consumers need to know.
- `do_ingest_document_schema()` (`smos/tools/ingest_tools.py`) is not atomic across
  its many separate `VectorStore._lock` acquisitions — two concurrent schema-ingests
  of the same `doc_id` could interleave checkpoint reads/writes. Not a live risk
  today (the MCP server is effectively single-threaded), flagged as a known boundary
  by the Phase 5.3 final review, not yet a scheduled fix.
- A re-sync (re-ingesting an already-complete `doc_id` with a changed `doc_name`)
  does not update the `document` graph node's name — `GraphStore.add_node` is
  `INSERT OR IGNORE` and the re-sync path intentionally leaves the document node
  intact while replacing its segments underneath. Acceptable for Phase 5.3 per the
  final review; revisit if `doc_name` drift becomes user-visible.

## Test commands

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts, not `def test_*`
suites. Don't point a regression gate at one and expect `pytest` to run it.

On this machine, if a `pytest` run reports **"No tests collected"** unexpectedly,
retry with `rtk proxy pytest tests/ -q --tb=short` before assuming something broke.

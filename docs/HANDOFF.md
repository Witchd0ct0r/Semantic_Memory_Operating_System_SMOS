# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-25)

**Phase 5.3 — Ingestion contract + `ingest_tools.py` integration: COMPLETE**, merged
to `main` at commit `a18fa4b` (feature branch `worktree-phase5-3-ingestion-contract`,
7 tasks + 1 final-review fix wave, fast-forward merge, branch deleted after merge).

Builds `smos/ingest/contract.py`: a versioned Pydantic ingestion schema
(`IngestionPayload`/`ExternalHandle`/`ContentBlock`/`SourceLink`) with shape-only
boundary validation (`validate_payload`) and quarantine-not-reject persistence for
invalid payloads (new `ingest_quarantine` SQLite table), plus a `ContractBlockAdapter`
that feeds the contract's already-typed block boundaries directly into 5.2's
segmentation cascade (no regex re-detection). `do_ingest_document_schema()`
(`smos/tools/ingest_tools.py`) is the resumable orchestrator: deterministic-id upsert
(`derive_doc_id(system, native_id)`) branches into fresh-ingest / resume-in-progress /
re-sync-on-content-change, storing leaves in checkpointed 64-leaf chunks (new
`ingest_checkpoints` SQLite table) so a mid-document crash never loses prior work or
leaves the containment tree half-wired. Registered as MCP tool `tool_ingest_document`
(`smos/server.py`). A minimal reference transformer (`markdown_to_payload`,
`smos/ingest/transformers/`) proves the client side of the contract end-to-end with a
byte-exact round-trip test. Test suite grew 227 → 265, all passing on `main`.

- **Pending-edge resolution deliberately deferred to 5.5** — `source_links` is
  shape-validated (edge types checked against the adapter registry) but not resolved
  into graph edges; no adapter populates real cross-references yet
  (`MarkdownAdapter.extract_references()` is still a no-op), so building resolution
  machinery now would have nothing real to exercise it.
- **`build_span_tree()`** (new public function, `smos/ingest/segmenter.py`) was
  extracted from `ingest_document_text` as a small, behavior-preserving refactor —
  shared by both the original 5.2 orchestrator and the new checkpointed one. All
  existing 5.2 tests pass unchanged.

**Bug found and fixed during the final whole-branch review (not by guessing — a
dedicated opus-model review caught it, a fix wave addressed it, a scoped re-review
confirmed):** the checkpointed chunk loop committed a chunk's segments
(`store.store_batch`) *before* advancing the checkpoint (`store.save_checkpoint`) —
a crash in that window left the chunk's segments stored but unacknowledged by the
checkpoint, so a resume would re-store the same chunk under new UUIDs, leaking
duplicate orphan segments. Fixed by reconciling `store.get_segment_ids_by_doc(doc_id)`
against the checkpoint's recorded `segment_ids` on every resume, deleting any
orphans before continuing. A second finding — resume never verified
`checkpoint["total_leaves"] == len(leaves)` after recomputing segmentation — is now
guarded: a mismatch discards the checkpoint and restarts fresh rather than silently
misaligning stored segment ids to the wrong char ranges. Both fixes are covered by
real (non-mocked) tests in `tests/test_contract_ingest.py`.

**Execution notes:** subagent-driven-development, in a worktree created via
`EnterWorktree`. Same base-ref gotcha as 5.1/5.2 flagged again — `EnterWorktree`'s
`fresh` default branched from stale `origin/main`; fixed with `git reset --hard main`
before dispatching Task 1. All 7 per-task reviews done **inline by the orchestrator**
(no separate reviewer subagent — see `feedback-inline-reviews`), plus one broad
final whole-branch review on the most capable available model before merge, per the
subagent-driven-development skill's process. **One process incident:** Task 7's
implementer pushed an unrequested second commit rewriting this HANDOFF file with
fabricated details (a nonexistent `segment_checkpoint_path` symbol, an invented
"migration guard"/"soft-delete workaround" that was never built) — reverted via
`git reset --hard` back to Task 7's real deliverable before continuing. Lesson: even
when a task brief is narrowly scoped, an implementer subagent may still wander into
adjacent docs files unprompted; the orchestrator must diff-review every commit a
task produces, not just the ones the brief asked for.

At `EnterWorktree`'s cleanup step (`ExitWorktree action: remove`), the tool reported
"77 commits... will discard this work permanently" — a false alarm caused by the
tool comparing against the worktree's original (stale) branch point rather than the
already-fast-forward-merged `main`. Verified `git rev-parse main` and
`git rev-parse worktree-phase5-3-ingestion-contract` were identical (zero unique
commits) before confirming `discard_changes: true`. Future sessions hitting this
warning after a fast-forward merge should do the same check before trusting the
commit count.

## Prior phases

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

## Next up — two independent tracks available

**Track A — Phase 5.4: Two-stage retrieval.** Natural continuation of 5.3.
Exact/verbatim retrieval strategy, ±1 neighbor expansion for context, and
`reconstruct()` to rebuild document passages byte-exactly from segment indices.
Depends on 5.1 (shipped). See the sub-phase breakdown below for 5.5 onward.

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
| **5.2** | Segmentation cascade (structural → semantic → size-fit), containment tree (`section` node type, `ingest_document_text`) | 5.1 | **DONE** (`5f42dd7`) |
| **5.3** | Ingestion contract (Pydantic schema, quarantine validation) + `ingest_tools.py` integration, resumable-checkpoint large-payload path | 5.1, 5.2 | **DONE** (`a18fa4b`) |
| **5.4** | Two-stage retrieval: exact/verbatim strategy, ±1 neighbor expansion, `reconstruct()` | 5.1 | queued |
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

**Next actions for a fresh session on Track A (5.4 two-stage retrieval):**
1. Read `docs/superpowers/plans/2026-07-25-phase5-3-ingestion-contract.md` for the
   pattern 5.3 used (task structure, TDD steps, the final-review fix wave) — 5.4's
   plan should follow the same discipline.
2. Write a detailed, **tree-verified** implementation plan for 5.4 — read current
   signatures from the actual files (`smos/ingest/contract.py`,
   `smos/ingest/segmenter.py`, `smos/memory/vector_store.py`, in particular
   `_query_internal()`'s metadata-field asymmetry noted below), not from this
   handoff or the spec (see the Phase 1 plan-drift lesson in `bm25-phase1-lessons`).
3. Slice into self-contained Haiku-dispatchable tasks per the CLAUDE.md workflow.
   Harden every implementer dispatch prompt with an explicit working-directory
   self-check up front (see "Execution notes" above for why), and diff-review every
   commit a task produces — not just the ones the brief asked for (see the Task 7
   incident above).

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

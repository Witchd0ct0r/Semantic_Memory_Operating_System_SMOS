# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-21)

**Phase 5.2 — Segmentation cascade: COMPLETE**, merged to `main` at commit `5f42dd7`
(feature branch `worktree-phase5-2-segmentation`, 7 commits, fast-forward merge,
branch deleted after merge).

Builds `smos/ingest/segmenter.py`: a structural → semantic → size-fit cascade
(Pillar B) over a pure `Span` tree (no I/O, unit-testable on its own), plus an
orchestrator (`ingest_document_text`) that stores each non-overlapping leaf as a
searchable `MemoryObject(type="segment", ...)` and wires a three-level containment
tree (`document` → `section` → segment) into the memory graph via `contains` edges.
Byte-exact reconstruction (concatenating ordinal-sorted leaves) is verified by a real
test, not just asserted.

- Added a `section` `GraphNode` type (same CHECK-widening migration pattern as 5.1's
  `document` addition) — the migration guard now keys off `'section'` presence, not
  `'document'`, so a 5.1-era DB (which already has `document` but not `section`) is
  correctly re-migrated instead of being mistaken for already-migrated.
- `_build_span_tree` recurses structural boundaries first (falls through to nested
  heading levels automatically), then sentence-embedding-based semantic valley
  detection, then fixed-size windowing as the last-resort fallback — matching the
  spec's cascade ordering exactly. Layer 4 (optional LLM-proposed boundaries) is
  explicitly deferred, not implemented in this slice.
- `section` graph nodes are pure containers (no VectorStore row of their own) —
  created only when a top-level structural child needed further splitting; a
  single-leaf top-level child links directly under `document`, skipping a redundant
  section layer. This is required by the byte-exact-reconstruction invariant: a
  section can't *also* be separately embedded/stored, or reconstruction would
  duplicate text.
- Test suite grew 207 → 227, all passing on this branch.

**Bugs found and fixed during implementation (both via TDD, not by guessing):**
1. `GraphStore.traverse()` is intentionally bidirectional (undirected BFS — correct
   for Phase 2/3's entity-relation use case), which means a `contains` traversal from
   a `section` node returns **both** its parent `document` and its leaf children in
   one hop. Documented on `_wire_containment`'s docstring; any future caller wanting
   only a section's children must filter by node type (a section's only non-`memory`
   neighbor is always its parent, by construction of the three-level cap).
2. The undersized-fragment merge logic (`_merge_undersized`) had a content-loss bug:
   absorbing a small leaf into a neighbor that already had its own children (from
   earlier splitting) silently dropped the absorbed range from `_flatten_leaves`,
   since it only ever walks `.children`, never a parent's raw start/end. Fixed via a
   new `_absorb_span` helper that explicitly materializes each side's range as a real
   leaf before splicing, instead of just stretching bounds. Caught by the plan's own
   reconstruction test failing (`# Title\n` silently missing from a reconstructed
   document) — a good example of why the byte-exact-reconstruction test earns its
   keep.

**Execution notes:** subagent-driven-development, in a worktree created via
`EnterWorktree`. Same base-ref gotcha as 5.1 flagged — `EnterWorktree`'s `fresh`
default branched from stale `origin/main` (47 commits behind); fixed with
`git reset --hard main` before dispatching Task 1. All reviews done **inline by the
orchestrator** (see `feedback-inline-reviews`) — except Task 1's, whose reviewer
subagent had already completed by the time the user corrected course, so that one
result was kept as-is. **Two process incidents worth flagging for future SDD
sessions on this repo:**
- Task 2's implementer subagent committed its work directly onto `main` in the
  primary checkout instead of the isolated worktree, despite an explicit "work from
  \<path\>" instruction in the dispatch prompt — text instructions in a prompt do not
  guarantee a subagent's tool calls actually run in that directory. Recovered by
  cherry-picking the stray commits onto the worktree branch and hard-resetting `main`
  back to its pre-session state (nothing had been pushed, so this was fully
  recoverable). Task 3's dispatch was hardened with an explicit "verify `pwd` and
  `git branch --show-current` before any git command" instruction and did not repeat
  the mistake.
- Task 3's implementer subagent invoked `python3`, which on this machine resolves to
  the Windows Store app-execution-alias stub rather than the real interpreter — it
  then tried to "fix" this by attempting to install a Python version manager,
  repeatedly popping up the Microsoft Store on the user's screen. Stopped via
  `SendMessage`; documented in the new `docs/runbooks/environment.md` (use `python`
  or `pytest`, never `python3`; a subagent should never try to install anything to
  work around a tooling/PATH issue — escalate instead).

## Prior phases

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

**Track A — Phase 5.3: Ingestion contract + `ingest_tools.py` integration.** Natural
continuation of 5.2. Pydantic ingestion schema (Pillar G) with quarantine validation,
wiring `ingest_document_text()` into the actual file-ingestion pipeline, and the
resumable-checkpoint large-payload path. Depends on 5.1 + 5.2 (both shipped). See the
sub-phase breakdown below for 5.4 onward.

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
| **5.3** | Ingestion contract (Pydantic schema, quarantine validation) + `ingest_tools.py` integration, resumable-checkpoint large-payload path | 5.1, 5.2 | next |
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
1. Read `docs/superpowers/plans/2026-07-21-phase5-2-segmentation-cascade.md` for the
   pattern 5.2 used (task structure, migration approach, TDD steps, and the two bugs
   found — see "Where things stand" above) — 5.3's plan should follow the same
   discipline.
2. Write a detailed, **tree-verified** implementation plan for 5.3 — read current
   signatures from the actual files (`smos/ingest/segmenter.py`'s
   `ingest_document_text` in particular), not from this handoff or the spec (see the
   Phase 1 plan-drift lesson in `bm25-phase1-lessons`).
3. Slice into self-contained Haiku-dispatchable tasks per the CLAUDE.md workflow. Given
   this session's incidents, harden every implementer dispatch prompt with an explicit
   working-directory self-check (see "Execution notes" above) up front, not just for
   tasks that seem risky.

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

## Test commands

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts, not `def test_*`
suites. Don't point a regression gate at one and expect `pytest` to run it.

On this machine, if a `pytest` run reports **"No tests collected"** unexpectedly,
retry with `rtk proxy pytest tests/ -q --tb=short` before assuming something broke.

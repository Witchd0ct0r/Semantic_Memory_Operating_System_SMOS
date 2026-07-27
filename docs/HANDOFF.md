# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-07-27)

**Phase 5.5a — Reference extraction & domain tagging: COMPLETE, reviewed clean, not
yet merged to `main`** (branch `worktree-phase5-5a-reference-extraction-domain-tagging`,
based on `main`@`f12d7f6`). Built via subagent-driven-development in a worktree, 5 tasks,
every task reviewed inline by the orchestrator (per session preference — no
task-reviewer subagents dispatched this session) plus a final whole-branch review, all
clean. **296/296 tests passing.** Test suite 283 → 296.

Commits (on the worktree branch, not yet merged): `5a0fe40` (`MarkdownAdapter.extract_references()`:
real link/wiki-link/self-topic-triple extraction, replacing the no-op stub;
`edge_types` now `frozenset({"references"})`), `849aeb7` (`_wire_references()` added to
`smos/ingest/segmenter.py` — turns extracted triples into `segment --edge_type-->
concept:{name}` graph edges via a 300-char lookback window; wired into
`ingest_document_text()`), `9d609cd` (the same `_wire_references()` wired into the
production schema-ingestion path, `do_ingest_document_schema()` in
`smos/tools/ingest_tools.py`), `6878e03` (fixes a separate, previously-silent gap:
`IngestionPayload.domain_tags` is now actually persisted — onto the `document` graph
node's metadata and onto every segment's tags — instead of being parsed and dropped),
`56686c1` (end-to-end proof: a markdown doc's cross-reference link graph-rescues its
target segment through `context_builder.py`'s existing 2-hop expansion with zero
changes to that file, and domain-tagged segments are retrievable via
`query_domain(domain_tags=...)`).

Closes the concrete gap flagged in this file since Phase 5.3: the Phase 2 memory graph
was inert on ingested documents because no adapter populated `extract_references()`.
Markdown documents with internal links or wiki-links now populate the graph the same
way backtick-marked code entities always have (`VectorStore._update_graph()`'s
`memory --references--> entity:{name}` shape, reused here as `segment --references-->
concept:{name}`).

**One real gap found and fixed during this session, not by the plan's original design:**
the schema-ingestion path delivers a heading and its body as *separate* `ContentBlock`s
(by the ingestion contract's own design, Pillar G), so a naive per-leaf-only extraction
call would never see the heading for a body leaf. Fixed with a fixed 300-char lookback
window into `full_text` before each leaf (`_REFERENCE_LOOKBACK_CHARS` in
`segmenter.py`) plus taking the *last* heading match in whatever text is given (not the
first) — verified empirically against the real pipeline before the plan was written,
and again by each task's passing tests.

**Note on this session's process:** Task 5's implementer pushed an unrequested second
commit editing this very file, with several factually wrong task-to-commit
attributions (mislabeling Task 2 as "markdown_transformer.py wiring" when it touched
`segmenter.py`; Task 3 as "domain_tags field" work when that was Task 4; Task 4 as
building a "graph-domain index" that was never built) plus a premature "ready to
merge" claim before the final review had run. Discarded via `git reset --hard` back to
the requested test-only commit; this write-up was authored by the orchestrator instead,
per the plan's own "Post-plan: HANDOFF update" step being reserved for the orchestrator,
not a task implementer.

**Deferred to 5.5b+ (Governor's 2026-07-27 scoping decision, made before this plan was
written):** domain **classification** (adapter rules + LLM fallback), the fan-out
evidence-gathering primitives (`resolve`/`classify_domains`/per-domain `recall`/
`coverage_check`), the coverage/gap signal, and new SE-specific traceability edge types
(`refines`/`satisfies`/`verified_by`/etc.) — deliberately held until there's a concrete
non-markdown adapter to validate them against, rather than bootstrapping speculative
machinery from markdown alone.

**Phase 5.4 — Two-stage retrieval (exact/verbatim recall, ±1 neighbor expansion,
`reconstruct()`): COMPLETE and MERGED to `main`** (merge commit `0ffc723`). Built via
subagent-driven-development in a worktree (branch
`worktree-phase5-4-two-stage-retrieval`, based on `main`@`2763b26`, now deleted —
worktree removed), all 6 tasks reviewed clean, plus a broad final whole-branch review
(opus model) that returned **Ready to merge: Yes** with zero Critical/Important
findings. Merge required resolving one expected trivial conflict in this file
(resolved by taking the branch's completion write-up). **283/283 tests passing on
`main` post-merge, verified.** Test suite 265 → 283.

Commits (now on `main`): `dd43875` (VectorStore hybrid-query metadata parity fix +
`get_segment_by_ordinal`), `dc72f2c` (`smos/memory/exact_retrieval.py`:
`recall_exact()` + `expand_neighbors()`), `9eedb50` (`reconstruct()` appended to the
same module), `d677f66` (`"exact"` strategy wired into `recall_engine.py`'s dispatch),
`dd2c0c2` (`tool_reconstruct` MCP tool + `tool_recall` docstring update), `f5934f9`
(end-to-end proof test, no production code), `0ffc723` (merge to `main`).

Gives SMOS the Stage 2 half of the two-stage retrieval loop (spec §7): `tool_recall(...,
strategy="exact")` returns unsummarized segments (confidence 1.0, no LLM compression)
plus their ±1 ordinal neighbors for context — the no-overlap segmentation's
replacement for RAG chunk overlap. `tool_reconstruct(node_id)` rebuilds a document's
or section's byte-exact original text by walking the containment tree and
concatenating segments in ordinal order. Closed a standing tech-debt item along the
way: `VectorStore._query_internal()` now returns the same full metadata
(`doc_id`/`ordinal`/`char_start`/`char_end`/`source_ids`/`confidence`) that
`get_by_uuid()` already did, so Stage 2 doesn't need a second per-candidate DB
round-trip to drive neighbor expansion.

**One design property flagged by the final review, not a defect:** `recall_exact`'s
`mode: "uncertain"` branch is only reachable on an empty store — FAISS returns nearest
neighbors unconditionally, so a query with no good matches against a non-empty store
still returns `mode: "extractive"`/`confidence: 1.0` over low-relevance segments.
Consistent with the plan's intent (verbatim recall trusts the ranker; "confidence"
here means "this is exact stored text," not "this is relevant"). Worth a docstring
note if it ever confuses a caller; not scheduled.

**Execution notes:** subagent-driven-development, Haiku implementers on
fully-code-specified tasks, task reviews on sonnet, final review on opus. Two process
incidents this session (both resolved, no code impact): Task 1's implementer
committed directly to `main` instead of the worktree (cherry-picked onto the worktree
branch as `dd43875`, `main` reset back to `2763b26`); Task 4's implementer's `Edit`
tool left a stray uncommitted duplicate in `main`'s working tree even though its git
commit landed correctly on the worktree branch (discarded via `git checkout --`). Full
detail in this plan's SDD ledger (deleted after merge per the skill's Finish step —
see git history of `docs/superpowers/plans/2026-07-25-phase5-4-two-stage-retrieval.md`'s
directory if ever needed). Fix applied to every dispatch from Task 2 onward: implementer
prompts now open with a mandatory `git branch --show-current` check, and explicitly
require absolute paths rooted at the worktree directory for every Read/Edit/Write call
(a plain `cd` in the Bash tool does not change where Edit/Write resolve paths).

## Prior phases

- **Phase 5.3 — Ingestion contract + `ingest_tools.py` integration** (merged
  `a18fa4b`, 2026-07-25): versioned Pydantic ingestion schema + quarantine validation
  (`smos/ingest/contract.py`), resumable checkpointed orchestrator
  (`do_ingest_document_schema`), `tool_ingest_document` MCP tool, markdown reference
  transformer. Test suite 227 → 265.
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

**Track A' — Phase 5.5+: multi-domain traceability & fan-out (Pillar F).** Natural
continuation of 5.4 — domain classification, typed edges, evidence-gathering
primitives. See the sub-phase breakdown below.

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
forces Phase 5 work to land before Phase 4. Track A' and Track B can proceed in
either order; pick based on which is more valuable next.

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
| **5.4** | Two-stage retrieval: exact/verbatim strategy, ±1 neighbor expansion, `reconstruct()` | 5.1 | **DONE** (`f5934f9`) |
| **5.5a** | Reference extraction & domain tagging: `MarkdownAdapter.extract_references()`, `_wire_references()` graph wiring (both ingestion paths), `domain_tags` persistence | 5.1, 5.3, 5.4 | **DONE** (`56686c1`), not yet merged |
| **5.5b** | Multi-domain traceability & fan-out (Pillar F): domain classification, typed edges, evidence-gathering primitives, coverage/gap signal | 5.5a, needs a non-markdown adapter | queued |
| **5.6** | Graph-propagation ranking fold (PageRank+RRF) | 5.5b (needs a populated graph) | queued |
| **5.7** | Clarification-loop cluster signal (Pillar E) | 5.4 | queued |
| **5.8** | Multilingual embeddings (optional extra, cross-cutting) | can land anytime before Phase 4 | queued |

**Where the work lands in the current tree** (spec §3 and §10 have the full detail).
`smos/tools/ingest_tools.py` now has two ingestion paths: the original code-repo
ingester (lossy per-file summary, unchanged) and the schema-driven
`do_ingest_document_schema()` path (structure-preserving, byte-exact). Domain
generality had one remaining gap, closed by 5.5a: `smos/memory/entity_extractor.py`
still remains Python-AST + backtick-spans only (extracting nothing from prose), but
the Phase 2 graph is no longer inert on markdown documents — `MarkdownAdapter` now
populates `extract_references()` for real, wired into both ingestion paths (see the
Phase 5.5a section above). `tests/bench_legal_regulations.py`'s docstring describing
this gap is now stale for markdown corpora specifically; still accurate for any other
domain until that domain gets its own adapter. The retrieval engine itself
(`context_builder.py`) is already domain-neutral and stays as-is — 5.5a's graph-rescue
mechanism required zero changes to it.

**Next actions for a fresh session on Track A' (5.5b multi-domain traceability):**
1. Merge the 5.5a branch (`worktree-phase5-5a-reference-extraction-domain-tagging`,
   based on `main`@`f12d7f6`) to `main` first, if not already done.
2. Read `docs/superpowers/plans/2026-07-25-phase5-4-two-stage-retrieval.md` for the
   pattern 5.4 used (task structure, TDD steps, the final-review fix wave) — 5.5b's
   plan should follow the same discipline.
3. Write a detailed, **tree-verified** implementation plan for 5.5b — read current
   signatures from the actual files, not from this handoff or the spec (see the
   Phase 1 plan-drift lesson in `bm25-phase1-lessons`). 5.5b needs a concrete
   non-markdown adapter (e.g. a legal or requirements adapter) before domain
   classification or the fan-out primitives can be validated against anything real —
   don't build them speculatively against markdown alone.
4. Slice into self-contained Haiku-dispatchable tasks per the CLAUDE.md workflow.
   Harden every implementer dispatch prompt with an explicit
   `git branch --show-current` self-check up front and a requirement to use absolute
   worktree-rooted paths for Read/Edit/Write (see "Execution notes" above for why),
   and diff-review every commit a task produces — not just the ones the brief asked
   for. 5.5a's Task 5 implementer pushed an unrequested HANDOFF.md edit with several
   wrong task-to-commit attributions; keep diffing the full commit list, not just the
   expected file.

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
- `GraphStore.traverse()`'s undirected BFS means a `contains` query from a `section`
  node returns its parent `document` alongside its real children — Phase 5.4's
  `reconstruct()` (`smos/memory/exact_retrieval.py`) already filters by node type
  (`type == "memory"`) to handle this correctly; any future containment-tree
  consumer needs the same filter (see `_wire_containment`'s docstring in
  `smos/ingest/segmenter.py`). Not a bug in `traverse()` itself (its bidirectionality
  is correct for Phase 2/3's entity-relation use case) — just a caveat.
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

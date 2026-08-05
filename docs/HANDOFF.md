# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone, without needing prior conversation history.
Keep it current — overwrite stale sections rather than appending a history log; the git
history is the log.

---

## Where things stand (2026-08-05, session 5)

**EU-law toolchain benchmark — COMPLETE (revised).** Full results in
`docs/eu-law-benchmark-results.md`. Design: `docs/superpowers/specs/2026-08-05-eu-law-benchmark-design.md`.
Plan: `docs/superpowers/plans/2026-08-05-eu-law-benchmark.md`. Real EUR-Lex corpus
(GDPR Chapter I + Regulation (EU) 2018/1807, English text), ingested via `LegalAdapter`
through the schema-driven path, 10 cross-document synthesis questions across **three**
runs: baseline (raw `Read`, 35,715 tokens), SMOS via a real MCP-tool conversation
(headless `claude -p`, 306,644 tokens — 8.6x the baseline), and SMOS via direct Python
calls added after the first pass (same `Agent`-tool mechanism as the 2026-07-27
Kubernetes benchmark's SMOS run, 41,447 tokens — only 1.16x the baseline). **Correction
to the first write-up:** the original "SMOS lost 8.6x on tokens" headline conflated
mechanism with retrieval quality — the Kubernetes benchmark's own SMOS run never used
the real MCP tool surface (no server was installed on that machine at the time; it used
the same direct-Python-call workaround added here), so the two benchmarks' original
numbers weren't comparable. Once mechanism is held constant, SMOS's token cost on this
small corpus is close to parity with raw reading (+16%), not 8.6x worse — the 8.6x
figure is real but specific to a long single-threaded MCP conversation's compounding
cache-read cost, not a general property of SMOS's tools. Quality-wise: MCP run 8/10,
direct-Python run 9/10 correct — including a genuinely interesting result on Q9, where
the *same* underlying store/graph produced a wrong answer via MCP and a correct one via
direct calls, suggesting the verified `LegalAdapter` graph defect below (a cross-regulation
"Article 3" concept-node collision) is a necessary but not sufficient cause — conversational
context accumulation across the MCP run's 20 turns looks like a contributing factor too.
Recommendation flagged for future work: `LegalAdapter`'s citation mechanism has no
per-regulation namespacing — worth hardening before trusting cross-reference graph-rescue
on any real multi-regulation corpus. No production code changed by this benchmark
(measurement only, per its design's scope).

**Phase 5.5b, slice 1 (legal adapter) — COMPLETE and MERGED to `main`.** Full
detail (commits, test delta, bench numbers, one investigated-and-confirmed-benign
deviation from the plan's predicted numbers) in "Phase 5.5b — Legal adapter (slice
1): COMPLETE and MERGED" below. **Next up:** 5.5c (multi-domain traceability &
fan-out — now unblocked, a concrete non-markdown adapter exists to validate
against), Phase 4 (HNSW migration, Track B), or hardening `LegalAdapter`'s citation
namespacing per this session's benchmark finding above — Governor's call, not yet
decided.

**smos MCP server did not connect in session 4 or session 5** — two sessions running
now (session 5 confirmed via `ToolSearch` mid-session, no `mcp__smos__*` tools
surfaced). Unlike session 3's transient blip (below, kept for reference), this isn't
a one-off — worth actually checking `claude mcp list` / `/mcp` status at the start of
the next session rather than assuming it's still just bad luck. Built-in Read/Grep/
Glob/Bash were used throughout both sessions instead. Not a policy violation
(nothing to opt into if the server isn't up), but if it fails a third session in a
row, treat it as a real regression to investigate, not another transient blip.

**smos MCP server: reconnect issue investigated, no bug found — resolved by a plain
`/mcp` retry (session 3).** Session opened with `/mcp` reporting "Failed to
reconnect to smos: ... timed out after 30000ms". Investigated: `claude mcp list`
showed it already `✔ Connected`, direct `import smos.server` (which triggers
`VectorStore` init — FAISS index + embedding model load) took ~7.6s, well under
the 30s timeout, no lock files or zombie processes contending for
`.smos/faiss.index`/`.smos/metadata.db`. Treated as a one-off transient blip (not
a code bug) per Governor's call; a plain `/mcp` retry reconnected cleanly
(`"Reconnected to smos."`) and the rest of that session used SMOS tools normally.
**If this recurs, don't re-diagnose from scratch** — the above already ruled out
slow startup, locks, and stale processes; look elsewhere (Claude Code's own MCP
client/timeout config) next time.

**Correction to this project's own SMOS policy application, worth knowing before
the next session reads any file:** this session was corrected mid-work for reading
several files with the built-in `Read` tool instead of
`tool_semantic_query`/`tool_read_file_compress`. This repo's `CLAUDE.md` "SMOS
active" policy is the *default* file-reading behavior for this project
specifically (opted in 2026-07-28, see "Live SMOS dogfooding setup" below) — it
does not wait for an explicit "use smos" cue the way the global
`~/.claude/CLAUDE.md` opt-in-per-session default does for other projects. Recorded
as a standing cross-session memory (`feedback-smos-active-by-default`) so this
isn't relitigated next session.

**Toolchain end-to-end benchmark: COMPLETE** (2026-07-27). SMOS tools vs. raw file
reading over a 20-page Kubernetes-docs corpus — full results and methodology in
`docs/toolchain-benchmark-results.md`. TL;DR: SMOS used 67% fewer tokens (41,869 vs.
128,324) at comparable-to-slightly-better answer quality on 8/10 questions, but with one
real synthesis hallucination (Q1) worth further investigation. Built in
`.worktrees/toolchain-benchmark` (branch `worktree-toolchain-benchmark`).

**Phase 5.5a — Reference extraction & domain tagging: COMPLETE and MERGED to `main`**
(fast-forward merge, `f12d7f6` → `ad9122f`, worktree and branch removed). Built via
subagent-driven-development in a worktree, 5 tasks, every task reviewed inline by the
orchestrator (per session preference — no task-reviewer subagents dispatched this
session) plus a final whole-branch review, all clean. **296/296 tests passing on
`main` post-merge, verified.** Test suite 283 → 296.

Commits (now on `main`): `5a0fe40` (`MarkdownAdapter.extract_references()`:
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

---

## Phase 5.5b — Legal adapter (slice 1): COMPLETE and MERGED to `main` (2026-08-03, session 4)

Spec: `docs/superpowers/specs/2026-07-28-phase5-5b-legal-adapter-design.md`. Plan:
`docs/superpowers/plans/2026-07-28-phase5-5b-legal-adapter.md`. Built via
subagent-driven-development in a worktree (branch
`worktree-phase5-5b-legal-adapter`, based on `main`@`1af69bd`, now merged and
cleaned up), 4 tasks, every task reviewed inline by the orchestrator (per session
preference — no task-reviewer subagents dispatched) plus a final whole-branch
review, also inline, both clean. **333/333 tests passing on `main` post-merge,
verified.** Test suite 318 → 333.

Commits (now on `main`): `f84a392` (`LegalAdapter` skeleton — provision-marker
structural grammar splitting on `Section N`/`Article N`/`Rule N`/`§ N`, same
boundary-to-next-boundary shape `MarkdownAdapter.structural_boundaries()` uses for
headings), `1f932f0` (`LegalAdapter.extract_references()` — citation
cross-references between provision markers, one-directional by design, plus
heuristic defined-term matching: a `_DEFINING_PATTERN_RE` "X means"/"X shall mean"
sentence emits a self-topic triple, and a broader `_TITLE_CASE_PHRASE_RE` 2-4-word
scan lets a segment merely *using* a term resolve to the same `concept:{name}` node
a defining segment created), `fb32a55` (end-to-end pytest proof,
`tests/test_legal_adapter.py`: a 2-provision legal document's definition segment
graph-rescues its separate usage segment through `context_builder.py`'s existing
unmodified 2-hop `edge_types=["references"]` expansion), `a57bf09` (new "3.
CROSS-REFERENCE RECALL VIA LEGAL ADAPTER" dimension in
`tests/bench_legal_regulations.py`: plain-prose cross-reference recall via the
schema-driven `LegalAdapter` path moves **33% → 100%** graph-off → graph-on),
`e490cd9` (commits the plan file itself — written last session but never
committed until this session's finish step).

**No changes to `smos/memory/entity_extractor.py`, `smos/compression/context_builder.py`,
or `smos/ingest/segmenter.py`** — the fix lives entirely in the new adapter file,
same pattern 5.5a used for `MarkdownAdapter`. Closes the concrete gap flagged since
5.5a: cross-reference graph-rescue now works on legal/regulatory prose the same way
it already works on markdown, without requiring a backtick-marking convention.

**Design decisions** (three Governor calls made via `AskUserQuestion` during
brainstorming, session 2):

1. **Adapter choice: legal/regulations**, not requirements — matches the
   `"consequences of X under Hungarian law"` driving use case in the parent design
   spec (§1). Governor's intent is to broaden to other non-markdown adapters (e.g.
   requirements) later; this is the first, not the only, one.
2. **Scope: bounded to adapter + reference extraction only** — proving
   cross-reference graph-rescue on legal prose closes the 0%-recall gap
   `tests/bench_legal_regulations.py` measured (see `legal-regulations-retrieval-gap`
   cross-session memory — now substantially superseded by this section's actual
   results). Domain classification, typed traceability edges, the fan-out
   primitives (`resolve`/`traverse`/`classify_domains`/`coverage_check`), and the
   coverage/gap signal are all deferred to 5.5c.
3. **Term-matching mechanism: heuristic phrase matching**, not a two-pass
   corpus-wide term registry — mirrors `MarkdownAdapter`'s existing self-topic-triple
   trick, chosen specifically to avoid changing the `extract_references()` /
   `_wire_references()` contract 5.5a shipped.

**Confirmed during design:** no `entity_extractor.py` change was needed — the
adapter's own `extract_references()` bypasses the generic backtick-only extractor
entirely for document-ingested segments, same as `MarkdownAdapter`.

**One real gap found and fixed during the design session** (not by the plan's
original design): the schema-ingestion path delivers a heading and its body as
*separate* `ContentBlock`s (Pillar G), so a naive per-leaf-only extraction call
would never see the heading for a body leaf. Fixed with a fixed 300-char lookback
window into `full_text` before each leaf (`_REFERENCE_LOOKBACK_CHARS` in
`segmenter.py`, unchanged by this slice — the lookback mechanism itself was 5.5a's
work, reused here) plus taking the *last* heading match in whatever text is given
(not the first) — verified empirically against the real pipeline before the plan
was written, and again by each task's passing tests.

**One deviation from the plan's predicted verification numbers, investigated and
confirmed benign:** the plan's Task 4 expected the new bench dimension's "Graph
off" recall to be **0.00 (0/3)**; the actual measured value is **0.33 (1/3)**.
Root cause (confirmed via `git stash` comparison against the pre-existing,
untouched dimension-2 code path, which shows the identical 0.33 baseline, and
independently re-verified by the orchestrator by reading `_CROSS_REF_SCENARIOS`
directly): the "Material Nonpublic Information / trading halt" scenario's query
("definition of confidential trading data") and target text ("**Trading** is
halted for any employee in possession of Material Nonpublic Information...")
share the token "trading", and the bench corpus for this dimension is only 8
documents — enough for BM25/vector lexical overlap alone to surface the target
even with the graph disabled. Pre-existing property of this one scenario/corpus
pairing (present identically before this slice existed), not a regression and not
implicating `LegalAdapter` — the substantive claim (graph-on closes the gap to
100% recall) holds exactly as designed. Worth hardening this one scenario's
noise/query pair in a future pass so all three cleanly show 0% graph-off; not a
blocker, not scheduled.

**Note on process (session 2, design/planning):** an earlier draft of this
write-up was clobbered by a task implementer pushing an unrequested second commit
editing this file with several factually wrong task-to-commit attributions, plus a
premature "ready to merge" claim before the final review had run — discarded via
`git reset --hard`; this file is authored by the orchestrator only, per the plan's
own "Post-plan: HANDOFF update" step, never a task implementer.

**Deferred to 5.5c:** domain **classification** (adapter rules + LLM fallback), the
fan-out evidence-gathering primitives (`resolve`/`classify_domains`/per-domain
`recall`/`coverage_check`), the coverage/gap signal, and new SE-specific
traceability edge types (`refines`/`satisfies`/`verified_by`/etc.) — deliberately
held until there's a concrete non-markdown adapter to validate them against (now
there is), rather than bootstrapping speculative machinery from markdown alone.

---

## Live SMOS dogfooding setup — COMPLETE (2026-07-28)

Governor's direction this session: stop treating SMOS-on-itself as validation-only,
and actually live-test it as this project's own daily-driver memory tool. Five tracks
identified during brainstorming; built via subagent-driven-development in a worktree
for the first two, direct orchestrator work for the rest (small, well-scoped fixes —
no worktree needed).

**Track 4 + 5 (the skill + product improvement) — merged to `main`** (fast-forward,
worktree `worktree-smos-doc-sync` based on `main`@`e8854c9`, both cleaned up after
merge). Spec: `docs/superpowers/specs/2026-07-28-smos-doc-sync-design.md`. Plan:
`docs/superpowers/plans/2026-07-28-smos-doc-sync.md`. Commits: `e3ed74e`
(`markdown_to_payload` now types markdown lists/tables as their own `ContentBlock`s
instead of one opaque paragraph blob — zero schema change, `ContentBlock.block_type`
already allowed `"list"`/`"table"`, the transformer just never emitted them), `f32ad12`
(new project skill `.claude/skills/smos-doc-sync/`: `scripts/sync_docs.py` walks a
curated document list — `docs/HANDOFF.md`, `README.md`, `ARCHITECTURE.md`,
`CLAUDE.md`, `docs/runbooks/*.md`, specs, plans — hashes each doc's content, and only
re-ingests ones whose hash changed since `.smos/doc_sync_manifest.json`'s last
record; wired into `CLAUDE.md`'s session-continuity section to run at session end).
Test suite 296 → 305.

**Track 1 (global MCP install) — DONE, with a real bug found and fixed along the
way.** The installed `smos-mcp` was corrupted: `pip show smos-mcp` reported "not
found" while `smos`/`smos-server` console scripts still ran, because site-packages
had orphaned `~mos_mcp-*.dist-info` directories (pip's tilde-rename safety net from
an interrupted Windows uninstall, never finalized). That orphaned dist-info's
`direct_url.json` also showed the prior install came from `pip install
git+https://github.com/.../SMOS.git` pinned at the same stale commit as
`origin/main` — meaningless for this repo's workflow since origin has sat dozens of
commits behind local `main` for months. Fixed by revising `smos/install/cli.py`
(commit `a7464e9`, direct orchestrator work, no worktree): `_check_smos_package()`
now fails loudly on a missing/orphaned dist-info instead of silently registering a
phantom entry point; `_update()` reinstalls editable (`pip install -e .`) from a
local git checkout instead of the GitHub remote when one is detected
(`_is_local_checkout()`); `_write_claude_md()` is now idempotent (checks for the
marker block before appending, so re-running `smos setup` no longer duplicates the
global policy block); new `--skip-claude-md` flag for projects whose own `CLAUDE.md`
already carries the policy. Test suite 305 → 318 (13 new installer tests).
Then executed directly (not via the interactive `smos setup` wizard — it has a
blocking model-choice prompt unsuitable for a non-interactive shell): removed the
orphaned dirs, `pip install -e .` (verified editable, `direct_url.json` →
`file:///C:/private/semantic_memory`, no GitHub pin), `claude mcp add smos --scope
user -- smos-server`. **`claude mcp list` now shows `smos: smos-server —
✔ Connected`.** Global `~/.claude/CLAUDE.md` deliberately left untouched (Governor's
explicit instruction).

**Track 2 (policy flip) — DONE** (commit `7d19c85`). This repo's `CLAUDE.md` no
longer overrides the global policy with "validation only" — it now uses the standard
active SMOS file-reading policy on its own source too. The Phase 1
`_init_db_and_index()` hallucination (`bm25-phase1-lessons`) is kept as a documented
caveat in the new section ("spot-check before trusting for anything
security/logic-critical or before an Edit"), not as a reason to avoid the tools —
Governor's call: surfacing that kind of failure under real use is the point of a
live test.

**Track 3 (initial full-repo ingestion) — DONE.** Documents: covered by
`smos-doc-sync`'s first real run (Task 2 of the plan above) — every curated doc
ingested with zero quarantines. Code: new `docs/runbooks/scripts/ingest_code.py`
(direct `do_recursive_semantic_ingest()` call, no MCP dependency, `include_patterns`
restricted to code extensions so it never overlaps with the doc-sync skill's
markdown-only path) — 97/97 `.py`/etc. files ingested, 0 errors, ~33 minutes
(CPU-bound Ollama summarization, slower than the README's GPU-accelerated
benchmarks — expect this to run long). Spot-checked with a real `semantic_query()`
call against the freshly-ingested `vector_store.py` content.

**Resolved (session 3):** the doc-sync re-run to pick up docs edited *during*
session 2 (`CLAUDE.md`, `docs/runbooks/environment.md`) plus that session's handoff
write — confirmed done: 0 re-ingested, everything already matched the manifest
from the prior run. Session 4 (this session, SMOS unavailable — see "Where things
stand" above) did not re-run doc-sync; the next session with a connected SMOS
server should sync this handoff's own edits.

---

## Toolchain end-to-end benchmark — COMPLETE (2026-07-27)

Full results, methodology, and per-question scoring: `docs/toolchain-benchmark-results.md`.
Plan: `docs/superpowers/plans/2026-07-27-toolchain-benchmark.md`. Spec:
`docs/superpowers/specs/2026-07-27-toolchain-benchmark-design.md`.

**One material deviation from the original plan, worth knowing before touching this area
again:** no `smos` MCP server is registered anywhere on this machine (`claude mcp list`
confirmed empty) — the plan's headless-`claude -p`-with-MCP isolation mechanism for the
SMOS-tool run was unusable as designed. Rather than install SMOS globally (a user-scope
change affecting every Claude Code project on the machine) just to unblock a benchmark,
the Governor redirected the test to call `semantic_query()`/`recall()`/`reconstruct()`
directly (the same functions the MCP tools thinly wrap) via a small helper script,
`sandbox/toolchain-benchmark/scripts/smos_tools.py`. This tested the real retrieval
mechanism, not the MCP transport — a reasonable substitution for what this benchmark was
actually trying to measure, but means MCP-path overhead and a fair wall-clock comparison
(a resident server vs. this workaround's cold-Python-process-per-query cost) are still
untested. Also found and left unfixed (didn't affect the run): `reconstruct()` needs a
document/section graph node id, not a `semantic_query`/`recall` source (segment) UUID.

---

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

## Next up — three independent tracks available

**Track A' — Phase 5.5c: multi-domain traceability & fan-out (Pillar F,
remainder).** Slice 1 (legal adapter, 5.5b) is **DONE and merged** — see "Phase
5.5b — Legal adapter (slice 1): COMPLETE and MERGED" above. 5.5c (domain
classification, typed edges, evidence-gathering primitives, coverage/gap signal)
is now unblocked — a concrete non-markdown adapter exists to validate it against —
but needs its own design/plan session before executing; not started.

**Track C — `LegalAdapter` citation namespacing hardening (est. small, ~1 session).**
Flagged by the EU-law benchmark (session 5, see "Where things stand" above):
`LegalAdapter`'s citation extraction has no concept of which regulation/document an
"Article N" citation belongs to, so two different documents' same-numbered articles
collide into one shared `concept:article N` graph node — verified to cause at least
one real wrong retrieval answer on a two-regulation corpus. Fix direction: namespace
citation concept ids by `doc_id` or an extracted regulation identifier. Worth doing
before trusting cross-reference graph-rescue on any real multi-regulation corpus,
and before 5.5c builds further traceability machinery on top of the same mechanism.
Not started; no design session held yet.

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
forces Phase 5 work to land before Phase 4. Tracks A', B, and C can proceed in any
order; pick based on which is more valuable next. Track C is the smallest and most
narrowly scoped of the three, and arguably worth doing before Track A' (5.5c) since
5.5c would otherwise build new traceability machinery on top of the same
un-namespaced citation mechanism.

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
| **5.5a** | Reference extraction & domain tagging: `MarkdownAdapter.extract_references()`, `_wire_references()` graph wiring (both ingestion paths), `domain_tags` persistence | 5.1, 5.3, 5.4 | **DONE** (`56686c1`), merged to `main` (`ad9122f`) |
| **5.5b** | Legal adapter (slice 1 of Pillar F): `LegalAdapter` structural grammar + `extract_references()`, proving cross-reference graph-rescue on legal prose | 5.5a | **DONE** (`a57bf09`), merged to `main` |
| **5.5c** | Multi-domain traceability & fan-out (Pillar F, remainder): domain classification, typed edges, evidence-gathering primitives, coverage/gap signal | 5.5b | queued |
| **5.6** | Graph-propagation ranking fold (PageRank+RRF) | 5.5c (needs a populated graph) | queued |
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

**Next actions for a fresh session on Track A' (5.5c, remainder of Pillar F):**
5.5b (legal adapter) is **done** — 5.5c needs its own brainstorming/design pass
before a plan can be written; don't assume the deferred scope from 5.5b's design
spec (§6) is still current without re-checking it against whatever 5.5b actually
shipped.
1. Start with `superpowers:brainstorming` against the parent design spec's Pillar F
   section and the "Deferred to 5.5c" list in the 5.5b write-up above (domain
   classification, typed traceability edges, fan-out primitives, coverage/gap
   signal) — this sub-phase has no design doc yet, unlike 5.5b going into its
   session.
2. Whichever worktree gets created, check `git log --oneline -3` inside it before
   trusting it (this repo's `origin` is dozens of commits behind local `main`; a
   fresh worktree's default base-ref can silently start from a stale baseline —
   recurred 3x now including this session, see `bm25-phase1-lessons` /
   `phase5-domain-general-ingestion` cross-session memory — fix each time is a
   `git reset --hard` to local `main`'s actual tip before doing anything else).
3. Harden every implementer dispatch prompt with an explicit
   `git branch --show-current` self-check up front and a requirement to use absolute
   worktree-rooted paths for Read/Edit/Write, and diff-review every commit a task
   produces — not just the ones the brief asked for. Several prior sessions'
   implementers pushed unrequested HANDOFF.md edits with wrong attributions; keep
   diffing the full commit list, not just the expected file, every task,
   indefinitely.
4. **This project's SMOS policy is active by default** (see "Where things stand"
   above) — use `tool_semantic_query`/`tool_read_file_compress` from the first file
   read of the session if the MCP server is actually connected (check `/mcp` or
   `ToolSearch` first — it failed to connect at all in session 4, see above).

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

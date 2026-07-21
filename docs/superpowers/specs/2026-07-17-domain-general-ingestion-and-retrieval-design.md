# Domain-General Document Ingestion & Structure-Aware Retrieval — Design (DRAFT)

**Status:** **All §11 open decisions resolved by the Governor (2026-07-21)** — see the
resolutions recorded inline in §11 below. Implementation is now unblocked. Still a **living
document**: refine in place and keep the revision log current.

**Phase:** Phase 5 — "Domain-General Ingestion & Structure-Aware Retrieval". A **distinct
workstream from Phase 4 (HNSW)**; see §2 for the sequencing constraint (multilingual before
HNSW).

**Next step for a fresh session:** write a detailed **tree-verified** implementation plan (read
real signatures from the actual files — not from this spec) before dispatching any
implementation tasks. See `docs/superpowers/plans/` for the plan once written.

**Diagrams:** `diagrams/ingestion-pipeline.svg` · `diagrams/two-stage-retrieval.svg` ·
`diagrams/multi-domain-evaluation.svg`.

**Roadmap pointer:** `docs/superpowers/plans/2026-06-30-smos-roadmap.md` (Phases 1–4).
**Builds on:** Phase 1 (BM25 hybrid), Phase 2 (Memory Graph), Phase 3 (Agent Primitives).

---

## 1. Goal — the target workflow

Let an agent answer a hard question over a large, pre-ingested body of knowledge with a
**progressive, two-stage** retrieval loop, keeping the agent's context far leaner than a
regular document search would:

1. User asks a hard question (e.g. *"What are the consequences of killing somebody in
   Hungary?"*, or *"Which requirements govern password rotation?"*).
2. Agent queries the tool. The tool already holds the relevant corpus (statutes,
   a requirements spec, RFCs, contracts, medical guidelines — **any domain**).
3. Tool returns a **lean overview** — the best segments across *several* documents,
   compressed.
4. Agent asks the user **clarifying questions** to narrow the case (self-defense?
   premeditated? victim's age? / which product, which regulation?).
5. Agent re-queries with the refined question.
6. Tool returns the **exact data** — the precise verbatim provisions/clauses, with the
   ability to reconstruct the surrounding document if needed.
7. Net: the agent's context is far leaner than dumping raw documents into it.

**This is explicitly domain-general.** It must work for legal, requirements, and other
fields *without per-domain forks in the core*. Domain knowledge is injected through a
thin adapter layer (§4), not baked into the pipeline.

---

## 2. Relationship to the existing roadmap

- **This is not Phase 4.** Phase 4 (HNSW migration) is pure search *performance*
  (O(log n), <10ms at 100K vectors). It does nothing for this workflow. Finishing the
  current roadmap does **not** deliver this goal — it is net-new work.
- **Sequencing caveat:** if multilingual embeddings (§9) are in scope, do that **before**
  HNSW. Swapping the embedding model changes vector dimensionality and forces a full
  re-embed/reindex; doing it after an HNSW migration means rebuilding the index twice.

---

## 3. What's already general vs. what's domain-coupled

**Already domain-neutral and largely done** — the retrieval *engine*: embeddings + BM25 +
RRF fusion + LLM compression (`context_builder.py`) don't care whether the text is
statutes or requirements. The "lean context" (goal step 3) and "leaner than doc search"
(step 7) properties already fall out of this and are benchmarked (`token_efficiency_*`).

**Domain-coupled today, and biased toward *code repos*** — exactly the two stages this
design has to fix:

| Component | Today | Problem |
|---|---|---|
| `smos/tools/ingest_tools.py` | Code-repo ingester: code+doc extensions only, extracts Python/JS imports, **stores one lossy summary per file**, `summarize=True` default | No structural chunking; exact text unrecoverable; no PDF/DOCX/HTML |
| `smos/memory/entity_extractor.py` | Python AST + backtick spans only | Extracts nothing from prose → the Phase 2 graph is inert on documents |

The fix is to **generalize these two behind an adapter interface**, not to add a legal
module (then a requirements module, then...). See `tests/bench_legal_regulations.py`,
whose docstring already documents the "graph is inert on unmarked prose" gap.

---

## 4. Pillar A — Domain-adapter interface

A thin, pluggable adapter supplies *only* the domain-specific knowledge; the generic core
does everything else. Target property: **domain #2 is a config/grammar file, not a code
change.**

An adapter provides:

- **Structural grammar** — how this domain's documents are natively segmented and nested
  (legal citation/numbering grammar; requirement ID + clause grammar; markdown heading
  rules). Consumed by the segmenter's Layer 1 (§5).
- **Extraction rules** — the domain's identifiers and cross-references (legal citations &
  defined terms; requirement IDs + traceability keywords like "satisfies", "verified by";
  code symbols via AST). Populates graph `references`/`depends_on` edges.
- *(Optional)* an **LLM prompt** for boundary/entity proposal on unstructured input.

The core segmenter, extractor, storage, and retrieval remain domain-agnostic. **Design
risk to hold the line on:** resist special-casing the first domain we try — the whole
value is the adapter staying thin.

---

## 5. Pillar B — Structure-aware auto-segmentation (not naive RAG)

Segmentation follows the document's **own** structure and treats fixed-size windowing as a
last resort. A cascade, most-meaningful boundary first:

1. **Structural boundaries (deterministic, primary).** Honor the author's own units before
   any ML. Markdown/HTML → heading/list/table tree. DOCX → real paragraph + style
   metadata. PDF → layout / heading-style cues. Legal → numbering grammar
   (`§14`, `Article 9(2)(b)`, `(a)`, `(1)`). Requirements → IDs and clause numbers. This is
   lossless, reversible (keep exact char offsets), and reproduces the intended units — a
   whole clause, a whole requirement, a whole subsection. Does 80–90% of the job on
   well-structured corpora.
2. **Semantic boundary detection (structure flat/too coarse).** Embed sentence-by-sentence,
   place a boundary where consecutive-sentence similarity drops (a "semantic valley" —
   TextTiling idea). Groups sentences actually on the same topic instead of cutting at an
   arbitrary token count.
3. **Constraint enforcement (fit + floor).** If a segment overflows the embedding window,
   recursively sub-split — next structural level down (sub-clause → sentence), then
   semantic, and *only then* fixed-size. Fixed-size is the fallback-of-the-fallback inside
   an over-long atomic unit, never the default. Merge undersized fragments (a lone heading)
   into a neighbor.
4. **LLM-proposed boundaries (optional, hardest cases).** Reuse the local Ollama model to
   mark where each distinct provision/requirement/topic begins. Opt-in (cost);
   domain-general because the *adapter* supplies the prompt.

**No overlap.** Standard RAG adds token overlap to avoid boundary loss; that duplicates
text and breaks lossless reconstruction. Because boundaries are meaningful here, context is
recovered instead via retrieval-time ±1 **neighbor expansion** (§6, §7). Structure-aware
boundaries + neighbor expansion replaces overlap.

**Segmentation produces the containment tree** — it is not a separate step. Parsing
structure yields a hierarchy (document → section → paragraph); store a node per level with
`contains` edges. Retrieval hits the paragraph; reconstruction assembles at any level.

---

## 6. Pillar C — Segment storage & linking model

Additive to the Phase 2 graph.

- **Segment = a `memory` node holding its EXACT text** — verbatim, embedded, and
  searchable. (Effectively "don't summarize the chunk.") This collapses today's split
  between the searchable-but-lossy `memories` table and the verbatim store that is
  key-only and unsearchable.
- **Parent `document` node** — new node type added to the `graph_nodes` type CHECK set
  (today: entity/concept/memory/repo_component/snapshot/agent — no "document"). Additive
  migration.
- **`contains` edges:** document → section → paragraph (edge type already exists).
- **`references` edges:** segment → segment, including **cross-document** (a provision that
  uses a term → the segment that defines it). Populated by the adapter's extractor. This is
  what makes "retrieve a few paragraphs from several documents, linked" work.
- **Ordering & reconstruction metadata** on each segment: `doc_id`, `ordinal`, and
  `char_offset` span into the original. `ordinal` is authoritative for reconstruction
  (cheap: one SQL fetch + sort). `char_offset` enables **byte-exact** reconstruction (see
  §7 open decision).

Because segments are embedded individually, top-k hybrid retrieval **already** returns the
best segments across many source documents — "a few paragraphs from several documents"
is a free consequence of segment-level embedding, not extra machinery.

---

## 7. Pillar D — Two-stage retrieval + reconstruction

**Stage 1 — lean overview (exists, reused):** hybrid retrieval + LLM compression over the
top segments across documents. Returns a compact summary + source segment ids + confidence.

**Stage 2 — exact data (new):** given the refined query, return the **verbatim** segments
(no LLM compression), tightly scoped, plus the ability to reconstruct their surrounding
document. Note: exact data is inherently less compressible — the token win at this stage
comes from *scoping tightly to the right segments*, not from summarizing.

**Reconstruction:** `reconstruct(doc_id[, level])` — fetch the document's segments, order
by `ordinal`, concatenate verbatim. For byte-exact output, reassemble via `char_offset`.

**Retrieval-time neighbor expansion:** when a segment matches, optionally pull ±1 adjacent
segments (by `ordinal`) so a matched paragraph brings its immediate context — the
replacement for RAG overlap. This extends the existing graph-expansion step (which already
pulls `references` neighbors of the top hit).

**Architecture decision (consistent with Phase 3):** do **not** modify
`context_builder.py` for Stage 2 — it is the benchmark-gated hybrid path. Add the exact /
neighbor-expansion / reconstruction logic in `recall_engine.py` (new strategies) or a new
module, keeping the Phase 1/2 gated path byte-for-byte stable. Phase 3 created
`recall_engine.py` for exactly this reason.

### Retrieval-quality enhancement: graph-propagation ranking (recommended fold)

Turn the memory graph from a crude additive expansion (today: pull ±2 memories sharing an
entity with the single top hit) into a first-class **structure-aware ranking signal**. Run a
bounded **personalized PageRank / single message-passing pass** over the graph, seeded on the
query's top vector+BM25 hits, and fuse the resulting flow score with RRF:
`final = fuse(RRF_score, graph_flow)`. The final ranking becomes structure-aware, not just
text-similarity-driven.

**Why this option** (vs. cross-encoder / late-interaction / PRF):
- **Parameter-free, no new dependency** — pure linear algebra; respects the roadmap "no new
  runtime deps" constraint that rules out a cross-encoder model.
- **Compounds with Pillars A/F** — the more the generalized extractor and traceability edges
  populate the graph, the more propagation pays. It *amplifies* that investment rather than
  duplicating it.
- **Aligned with the multi-domain use case** — relevance flows across
  `references`/`complies_with`/`verified_by`/`refines`, so a requirement query surfaces its
  linked standards and parent requirements (Pillar F evidence gathering) in ranked order, not
  just a bounded ±2 pull.

**Expected gain:** large on connected-knowledge / multi-hop queries — generalizes the Phase 2
graph-rescue result (0%→100% with crude expansion) from the top hit's immediate neighbors to
the weighted neighborhood; negligible on flat single-doc lookups. **Gain is a function of edge
quality** — near-zero on today's inert-prose graph.

**Sequencing (important):** fold this **after** the graph-populating pillars (A generalized
extractor, F traceability edges) — never before, or it's a no-op.

**Cost / risk:** moderate effort; bound propagation to a local subgraph so latency stays flat;
additive, no index change, fits `recall_engine.py`/`graph_store.py`. Main risk is
over-propagation through hub nodes (a heavily-cited standard injecting noise) — controlled with
damping, edge-type weights, and degree normalization (same rationale as today's ±2 cap).

**Optional later precision step (not now):** a **cross-encoder rerank** gives a bigger raw
precision lift but costs a model dependency + per-candidate latency; if wanted, get equivalent
**listwise reranking from the existing Ollama model** via a rerank prompt, dodging the new
dependency. **PRF (Rocchio) deprioritized** — its query drift hurts the exact-citation /
exact-provision matching that legal and requirements corpora need. **Late interaction
(ColBERT-style)** has the highest ceiling but needs a storage/index overhaul — research spike,
out of scope now.

---

## 8. Pillar E — Clarification support (disambiguation)

The clarifying-question loop (goal step 4) is mostly agent-side, but the tool must give the
agent something to ask *about*. This is an **iterative, multi-turn** loop — not a single
round-trip — and it can move the topic, not just shrink the result set.

**Two distinct kinds of clarification** (they interleave and repeat):
- **Sense / branch disambiguation** — *which topic entirely*. A polysemous query
  ("Python" → programming language / snake / comedy troupe) matches candidates that fall
  into distinct, mutually-unrelated clusters. The agent should ask the user to pick a
  branch.
- **In-topic narrowing** — *once the topic is fixed*, refine the case (self-defense vs
  premeditated; which product/version/regulation). Candidates are one tight cluster
  differing on an attribute.

**Each iteration re-anchors, it does not just filter.** A refined query is a *fresh* Stage 1
retrieval (new embedding, new hybrid search), because the user's answer may reveal the real
topic is a different branch than the current candidates. A naive "intersect with the
previous result set" would trap the agent in the wrong branch. Accumulated answered
constraints are folded into the query, but the semantic anchor is allowed to move.

**Tool signal (what Stage 1 returns to drive this):** cluster the top candidate segments.
- If they split into **distinct clusters** (low inter-cluster similarity) → surface the
  clusters as competing senses → agent disambiguates the branch.
- If they form **one tight cluster** → surface the distinguishing attributes → agent
  narrows the case.

**Termination / convergence:** loop while candidates remain multi-cluster or confidence is
low; exit to Stage 2 (exact) once they converge to a single focused topic with sufficient
confidence. The loop counter and accumulated constraints live agent-side; the tool's job is
to keep each turn's cluster/facet signal rich enough to decide *disambiguate vs narrow vs
done*. Domain-general (no facet taxonomy hardcoded).

---

## 8.5 Pillar F — Multi-domain traceability & cross-domain evidence gathering

**Problem this adds (systems-engineering use case).** A requirement often can only be
evaluated across *several domains at once*. Driving example: *"What do we need to verify
this requirement?"* The agent must (1) **source** the requirement, (2) follow its
**traceability links** — parent/child requirements (Sys.1 stakeholder → Sys.2 system →
component), (3) determine the **domains** it touches (legal, technical, mechanical, safety,
EHS...), and (4) **fetch the governing documents per domain** (REACH, RoHS, applicable law,
stakeholder requirements, meeting notes, standards) to assemble the evidence needed to reach
a verification conclusion.

**Conceptual correction.** Pillars A–E treated a *domain* as a property of a **corpus** (one
adapter per corpus). This case shows a domain is *also a dimension of a single question*:
one requirement fans out across many corpora simultaneously, and the **links between them**
are the primary object of the query — not any one document. This does not replace the
adapter model; it adds a cross-corpus graph and a fan-out retrieval mode on top of it.

**Builds on what exists:** the Phase 2 graph (typed nodes/edges), cross-document `references`
edges, the pluggable extractor (Pillar A), one shared store holding all corpora. Additions:

1. **Typed traceability edges (adapter-supplied vocabulary).** The current edge CHECK set is
   code-oriented. SE needs `refines`/`derived_from` (Sys.2 ← Sys.1), `satisfies`,
   `verified_by`, `validated_by`, `allocated_to`, `complies_with` (requirement →
   standard/regulation), `impacts`. Make the edge vocabulary **extensible per adapter**
   rather than a fixed CHECK set.
2. **Domain tagging + requirement domain-classification.** Every artifact carries domain
   tags at ingest (legal / mechanical / EHS / stakeholder / meeting-note / standard). The
   *requirement itself* is classified into the **set of domains it touches** — adapter rules
   (e.g. "lead, cadmium, SVHC" → REACH/RoHS legal) plus LLM fallback. This is the
   "understand the domains" step, and it routes the fan-out.
3. **Fan-out / evidence-gathering strategy (new).** "What do we need to verify this"
   decomposes into a plan: resolve the requirement node → traverse typed traceability edges
   (up to Sys.1, down to components, out to linked standards) → classify domains → for
   **each** domain run the segment-level two-stage retrieval against its governing corpus →
   assemble an **evidence bundle**. Each item carries **provenance**: which domain, and the
   link path by which it was reached.
4. **Coverage / gap signal (new).** Verification needs completeness: flag domains with no
   governing document found, requirements with no `verified_by` link, and contradictory
   constraints across domains. Extends the Phase 3 `reflect` idea (gaps/contradictions) to
   the cross-domain evidence set — this is what lets the agent *drive a conclusion* rather
   than just retrieve.

**Architecture decision (the real one here): where does the fan-out orchestration live?**
- *Thin-tool:* expose primitives — `resolve`, `traverse(typed edges)`, `classify_domains`,
  per-domain `recall`, `coverage_check` — and let the **agent** orchestrate. Keeps the core
  general, matches the agent-side-loop philosophy from Pillar E.
- *Fat-tool:* one `evaluate(requirement)` that runs the whole fan-out server-side and returns
  an evidence bundle. Turnkey, but bakes an SE workflow into the tool.
- *Recommendation:* thin primitives + an optional **adapter-composed** `evaluate` convenience
  — general users get primitives, SE users get the composed flow via config. Same "keep core
  general, push workflow to the adapter" bias as everywhere else.

---

## 8.6 Pillar G — Integration & ingestion-contract integrity

**Deployment context.** SMOS runs as a standalone server; its tools are called over an API by
agents/scripts that gather data from source systems (Codebeamer, Jira, DOORS, Word, Excel,
PLM/ALM). SMOS stays integration-agnostic — it never learns any source system's format or
API. The boundary between client and tool is a **versioned ingestion contract (schema)**.

**Responsibility split (reconciled — this is the settled model).**
- **Client transformer (static, per source type):** authenticate to the source, extract
  content + metadata, and normalize into the schema's **structure-preserving** representation;
  carry source-native links and identity. Does *not* segment, embed, or build the semantic
  graph. New source system = new transformer, **zero SMOS changes** — the source-format /
  ALM-SDK / PDF·DOCX·XLSX dependencies live here, off SMOS (resolves the "no new deps" tension
  in §11).
- **SMOS:** validate the contract, then **segment, index, extract content cross-references, and
  build connections** — Pillars A/B/C/F run *inside* SMOS. Centralized so segmentation
  granularity and ordinals/offsets are uniform by construction, not drifting per client. SMOS
  mints segment ids, ordinals, and char-offsets.

**Why segmentation stays central.** If each client segmented, granularity would drift by source
and need a conformance suite to stay consistent; central segmentation is uniform by
construction and lets SMOS own byte-exact reconstruction offsets. **Precondition:** the schema
must deliver a *structure-preserving* payload — ordered typed blocks (heading+level, paragraph,
list, table), **not** flattened text — or SMOS's deterministic structural layer (Pillar B,
Layer 1) degrades to semantic/size splitting. Faithful structural normalization is therefore
the client transformer's core job; a flat-text dump would hand SMOS a *worse* starting point
than the source had.

**The ingestion schema must carry:**
- **Document identity** — deterministic id from `system + native_id` (re-sync = idempotent
  upsert, not duplicate insert).
- **External retrieval handle** — `system`, native id, resolvable deep link, and
  `version/baseline + source_updated_at`. Doubles as a **staleness anchor**: SMOS holds a
  derived copy while the source of record stays mutable, so the coverage/gap check (Pillar F)
  can flag stale evidence.
- **Structure-preserving content** — ordered typed blocks with heading hierarchy and tables;
  **verbatim** text required for anything servable as "exact"; optional **per-block source
  anchors** (ALM items expose sub-ids → per-segment deep links; monolithic docs fall back to
  doc-handle + reconstructed location).
- **Source-native links** — traceability that lives only in the source's link DB (DOORS trace,
  Jira relates, Codebeamer refs) as explicit edges. SMOS *cannot* re-derive these from text; it
  **merges** them with the content cross-references it discovers.
- **Semantics** — `type` (requirement/standard/stakeholder_need/meeting_note/…), `domain_tags`
  (Pillar F fan-out), free `metadata` (status, priority, author, timestamps, baseline).
- **Provenance / trust** — producing transformer + version; `confidence`/trust (verbatim-from-
  source vs agent-derived); `ingested_at`.
- **`schema_version`** — the contract is versioned so it can evolve without breaking clients.

**Boundary validation SMOS enforces (shape, not judgment):** verbatim present;
structure-preserving blocks present; valid edge types; resolvable identity + well-formed
external handle; block/segment sizes within limits; referential integrity on links (targets
resolve, else stored as *pending edges* resolved on later ingestion). Reject or quarantine on
failure — never silently store an unstructured blob that can't participate in
retrieval/traceability/reconstruction.

**Large-payload ingest (e.g. a 500-page doc in one call):** batch embedding; route through the
**batch** BM25 path (the single-item path is an O(n²) full-rebuild); scope dedup so distinct
clauses aren't collapsed; make ingest **atomic or resumable** so a mid-document failure doesn't
leave a half-linked document in the graph.

**Multi-client (if shared):** namespace/tenancy isolation (else corpora pollute each other's
retrieval); auth; the global `_lock` with `embed()` held inside it serializes writers — a
throughput ceiling under many concurrent API clients (ties to standing tech debt).

---

## 9. Cross-cutting: multilingual embeddings

`all-MiniLM-L6-v2` is English-oriented, 256-token window. Any non-English corpus (e.g.
Hungarian statutes) needs a multilingual embedding model, and BM25 tokenization needs a
matching review. **Dimensionality change → full re-embed/reindex** → do it before HNSW
(§2). Model choice TBD.

---

## 10. Schema & component impact (map to current tree)

Additive schema only (per roadmap constraint):
- `graph_nodes` type CHECK: add `document`.
- Segment metadata: `doc_id`, `ordinal`, `char_offset` (as tags/columns — mirror existing
  `tags`/`tier` patterns).
- Edge types: `contains` and `references` already exist — no new edge types required *for
  reconstruction* if it uses `ordinal` (not sequential `follows` edges).
- **Traceability edges (Pillar F):** SE link types (`refines`, `satisfies`, `verified_by`,
  `allocated_to`, `complies_with`, `impacts`) do need new vocabulary — provide it as an
  **adapter-extensible** edge set rather than a fixed CHECK constraint.
- **Domain tags** on artifacts/segments/nodes (controlled vocabulary per adapter) — drives
  Pillar F fan-out routing.
- **Ingestion contract (Pillar G):** external input is a versioned, structure-preserving
  schema (typed blocks + hierarchy + identity + external handle + carried source-native links +
  domain/type metadata). SMOS segments/indexes/links on top of it; clients never segment.

Components to create/change:
| File | Action |
|---|---|
| `smos/ingest/segmenter.py` (new) | The §5 cascade |
| `smos/ingest/adapters/` (new) | Domain adapters (grammar + extraction rules) |
| `smos/memory/entity_extractor.py` | Generalize to the pluggable extractor interface |
| `smos/tools/ingest_tools.py` | Format detection (add PDF/DOCX/HTML); segment + store verbatim; stop defaulting to lossy per-file summary |
| `smos/memory/vector_store.py` | Store verbatim segments as searchable; ordinal/offset metadata |
| `smos/memory/graph_store.py` | `document` node type; document→segment `contains` wiring |
| `smos/memory/recall_engine.py` | New strategies: exact/verbatim, neighbor-expansion, reconstruct; **Pillar F fan-out / evidence-gathering + coverage-gap check**; **graph-propagation ranking fusion (§7 enhancement)** |
| `smos/memory/graph_store.py` (Pillar F) | Adapter-extensible **traceability** edge vocabulary; domain-tagged nodes; typed-edge traversal |
| `smos/ingest/adapters/` (Pillar F) | Adapters also supply domain-classification rules + link-type vocabulary |
| `smos/memory/embeddings.py` | Multilingual model swap (dimension change) |
| `smos/ingest/contract.py` (Pillar G, new) | Pydantic ingestion schema + boundary validation (verbatim/structure/identity/handle/edge-type/referential checks) |
| `smos/tools/ingest_tools.py` (Pillar G) | Accept structure-preserving schema payload; segment on provided blocks; deterministic-id upsert; merge source-native + discovered links; pending-edge resolution; **atomic/resumable** ingest via **batch** embed + BM25 paths |
| `smos/server.py` | Register `tool_reconstruct` + exact-retrieval surface; **optional `tool_evaluate` (adapter-composed fan-out)**; **`tool_ingest_document` (schema-in) + namespacing** |
| `tests/bench_legal_regulations.py` | Already the proving ground; add a second-domain (requirements) bench + a **multi-domain traceability** bench to prove generality |

---

## 11. Open decisions — **RESOLVED by the Governor, 2026-07-21**

- **Byte-exact reconstruction? → YES.** Store `char_offset`; segmenter must be reversible.
  Needed for provably-exact verbatim quotes in legal/requirements domains.
- **Dedup scoping → excluded.** Ingested-document segments are excluded from the lifecycle
  `_deduplicate()` step (disabled on that ingest path) — near-identical clauses/requirements
  are legitimately distinct, not duplicates.
- **Containment depth → three levels** (document → section → paragraph). Matches how
  legal/requirements documents are actually structured; `reconstruct(level=...)` can target a
  meaningful mid-level, not just whole-doc or single-segment.
- **New-dependency tension → keep the constraint; multilingual is an optional extra.**
  PDF/DOCX/etc. parsing deps live entirely in client-side transformers (Pillar G), never in
  SMOS. The multilingual embedding model in `embeddings.py` is a swappable, lazy-imported,
  opt-in config (e.g. `EMBEDDING_MODEL=multilingual`) — not a default hard dependency. SMOS
  core adds zero new required runtime deps.
- **Ordinal vs. sequential edges → `ordinal` authoritative.** No `follows` edges; confirmed.
- **Edge-vocabulary extensibility (Pillar F) → open TEXT column + adapter-level validation.**
  `edge_type` becomes free TEXT; validity is enforced in application code against each
  adapter's declared vocabulary, not a SQL CHECK constraint. No migration needed per new
  adapter — matches the "domain #2 is a config file" design goal.
- **Fan-out orchestration line (Pillar F) → thin primitives + optional adapter-composed
  `evaluate()`.** General users get `resolve`/`traverse`/`classify_domains`/`recall`/
  `coverage_check`; SE users get the composed convenience via adapter config.
- **Domain-classification method (Pillar F) → hybrid, controlled vocabulary per adapter.**
  Adapter-supplied rules handle the obvious cases deterministically; ambiguous cases fall
  back to the local Ollama model. Domain tags are a controlled vocabulary per adapter, not
  free-form.
- **Graph-propagation ranking (§7 enhancement) → confirmed.** Personalized PageRank fused
  with RRF, sequenced strictly after Pillars A/F land (no-op before then, since it needs a
  populated graph). Damping factor, per-edge-type weights, degree normalization, and subgraph
  bound are implementation-time tuning, not a gating decision. Cross-encoder / Ollama
  listwise rerank remains a later optional step; PRF and late-interaction stay deferred.
- **Ingest transactionality (Pillar G) → resumable checkpoints.** A late failure partway
  through a large (e.g. 500-page) document doesn't lose all prior work; ingestion resumes
  from the last good checkpoint rather than restarting from scratch.
- **Deterministic identity vs. dedup (Pillar G) → external-key wins.** For schema-ingested
  records, external-key identity + idempotent re-sync takes precedence; similarity-based
  dedup is off on that path (consistent with the dedup-scoping resolution above).
- **External-handle granularity (Pillar G) → both, as designed.** Per-block source anchors
  where the source system exposes sub-ids; doc-handle + reconstructed-location fallback
  otherwise. Both paths defined in the contract.
- **Schema versioning & governance (Pillar G) → quarantine, not reject.** Non-conforming
  ingestion payloads are stored in a separate holding area with the validation error, rather
  than discarded — lets the client/operator inspect and fix without re-extracting from the
  source system. Contract + reference transformer still to be published alongside Pillar G's
  implementation.
- **Multi-tenancy & auth (Pillar G) → deferred entirely.** SMOS stays single-tenant, no
  namespacing, no auth. Revisit only if/when SMOS is actually deployed as a shared
  multi-client server.

---

## 12. Out of scope (for now)

- Phase 4 HNSW performance work (separate).
- Tier enforcement / eviction (still deferred from Phase 3).
- Confidence-weighted ranking.
- Retroactive backfill of pre-existing (pre-segmentation) memories.

---

## Revision log

- **2026-07-17** — Initial draft from design discussion: goal workflow, domain-general
  framing, adapter interface, structure-aware segmentation cascade, segment/parent-child
  storage + reconstruction, two-stage retrieval, multilingual note, open decisions. Living
  doc — more refinements pending from Governor.
- **2026-07-17 (edit)** — Expanded §8: clarification is an **iterative multi-turn loop**
  that can **pivot topic**, not a single narrowing round-trip. Distinguished sense/branch
  disambiguation (e.g. "Python" → language / snake / comedy) from in-topic narrowing; each
  iteration **re-anchors** with a fresh Stage 1 retrieval rather than filtering the prior
  set; added the cluster-based tool signal (distinct clusters → disambiguate; one tight
  cluster → narrow) and a convergence/termination condition.
- **2026-07-17 (edit 2)** — Added **Pillar F (§8.5): multi-domain traceability &
  cross-domain evidence gathering**, from the systems-engineering use case ("what do we need
  to verify this requirement"). Reframed "domain" as also a *dimension of a query*, not only
  a property of a corpus. Added typed/extensible traceability edges, requirement
  domain-classification, a fan-out evidence-gathering strategy with provenance, and a
  coverage/gap signal. New open decisions: edge-vocabulary extensibility, fan-out
  orchestration line (tool vs agent), domain-classification method. Updated §10 schema/
  components accordingly.
- **2026-07-17 (edit 3)** — Added §7 retrieval-quality enhancement: **graph-propagation
  ranking** (personalized PageRank fused with RRF) as the recommended training-free fold —
  parameter-free, no new dependency, compounds with the graph-populating pillars, sequenced
  **after** Pillars A/F. Recorded the rejected/deferred alternatives (cross-encoder →
  optional later, ideally via the existing Ollama model; PRF deprioritized for query drift;
  late-interaction out of scope). New open decision for its tuning; updated §10 components.
- **2026-07-17 (edit 4)** — Added **Pillar G (§8.6): integration & ingestion-contract
  integrity**, for standalone-server / API use. Settled the responsibility split: **client
  transformers** (static, per source system) normalize source content into a **versioned,
  structure-preserving schema**; **SMOS segments, indexes, extracts cross-references, and builds
  connections** centrally (Pillars A/B/C/F stay in SMOS). Specified the full schema (identity,
  external retrieval handle + staleness anchor, structure-preserving typed blocks, carried
  source-native links, semantics, provenance/trust, `schema_version`), the boundary validations
  SMOS enforces, large-payload ingest requirements (atomic/resumable + batch paths), and
  multi-client concerns (namespacing/auth/concurrency). Updated §10 schema/components and added
  five Pillar G open decisions. Note: this supersedes the transient "thin-SMOS / client-side
  segmentation" idea from discussion — segmentation is central; only source extraction is
  client-side.
- **2026-07-21** — Governor resolved all ~13 open decisions in §11 (recorded inline). Key
  outcomes: byte-exact reconstruction (store `char_offset`); three-level containment tree;
  multilingual embedding kept as an optional/lazy-imported extra rather than a hard dependency
  (SMOS core still adds zero new required runtime deps); open-TEXT adapter-validated edge
  vocabulary; hybrid (adapter-rules + LLM-fallback) domain classification; resumable-checkpoint
  ingest; quarantine (not reject) on schema validation failure; multi-tenancy/auth deferred
  entirely. Implementation is now unblocked — next step is a tree-verified implementation plan.

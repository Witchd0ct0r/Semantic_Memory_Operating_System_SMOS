# Requirements ingestion & context pack — design

Status: draft for review (2026-10-13). Phase 5.5c precursor; builds on 5.1–5.5b and Track C
(`a3c8575`, document-scoped citation concepts).

## 1. Goal

SMOS stays a standalone store with an API. A **third-party parser** turns a requirements
system (e.g. Codebeamer) and any normative sources (law, standards) into the existing
`IngestionPayload`; SMOS contains no Codebeamer-specific code.

A **local model** must be able to query SMOS about a requirement — an item already stored
or a draft — and receive the context it needs to judge whether it is a good requirement:
linked items, same-domain/topic peers, relevant legal provisions and standards, each with
relevance and provenance, within a token budget.

SMOS supplies evidence and plain coverage facts. It does **not** produce the verdict.

## 2. Reference model (what the parser sends)

Source of the link model: the Codebeamer API & requirement model reference doc. The parts
SMOS needs:

| Source concept | Direction / shape | Payload carrier |
|---|---|---|
| Association `depends` | `from` = dependent (downstream), `to` = upstream | `source_link` `edge_type: depends_on`, stored `from → to` as sent |
| parent / children tree | within one tracker | `edge_type: parent_of` (parent → child) |
| Epic (item-reference field) | M:N grouping | `edge_type: member_of` (item → epic) |
| Wiki cross-reference | text pointer | `edge_type: references` |
| Normative citation | requirement → clause | `edge_type: cites`, clause in `metadata` |
| Domain / ASIL / Variants | option values | `domain_tags` |
| Process level (SYS.1/2/3) | tracker | `domain_tags` entry `level:SYS.2` (tags are what retrieval filters on) |
| Status, tracker, version, priority | scalar attributes | `metadata` |
| Topic | local attribute, not a CB field | `domain_tags` entry `topic:<name>` |

Identity is `(system, native_id)` with `system` = instance host; `derive_doc_id` already
produces the deterministic doc id.

## 3. R1 — `source_links` become graph edges

**Current gap:** `IngestionPayload.source_links` is validated (edge_type must be in the
registered vocabulary) and then ignored by `do_ingest_document_schema`.

- Each `SourceLink` becomes a `document --edge_type--> document` edge. The target is
  `derive_doc_id(link.metadata.get("target_system", payload.external_handle.system),
  link.target_native_id)`.
- **Forward references:** if the target document node does not exist, create a
  placeholder `document` node (`metadata.placeholder = true`, name = native id). When the
  real item is ingested, the node is upgraded (name, metadata, tags). `GraphStore.add_node`
  is `INSERT OR IGNORE` today, so an explicit upgrade path is added (new `GraphStore`
  method or an upsert option); existing callers keep the current semantics.
- **Re-sync:** edges *owned by* a document (source = that document, created from its
  `source_links`) are replaced on re-ingest. Incoming edges from other documents are left
  alone.
- **Vocabulary:** a registered `RequirementsAdapter` (no structural or text-extraction
  behaviour; `structural_boundaries`/`extract_references` return empty) declares
  `parent_of`, `member_of`, `cites`. `depends_on` and `references` are already core.
  Registration is explicit, as with `LegalAdapter`.
- **`doc_type` persistence:** `IngestionPayload.doc_type` is also parsed and dropped today
  (same class of gap as `domain_tags` before 5.5a). R1 persists it on the `document` node's
  metadata and tags every segment with `doctype:<value>`, which is what `normative`
  filters on (tags are the field retrieval can filter by).
- Edges never target segments in slice 1 — item-to-item and item-to-document only.
  `cites` metadata (clause, optional note) is stored on the edge.

## 4. R2 — `tool_requirement_context`

New module `smos/memory/requirement_context.py`; MCP tool in `smos/server.py`.
(`tool_recall` takes flat string parameters; a dedicated tool is cleaner than adding a
strategy.)

**Inputs:** `text` | `item_id` (one required); optional `level`, `domain_tags`,
`declared_links` (list of `{target_native_id, edge_type, target_system?}`),
`token_budget` (default set in code), `k_per_section`.

- `item_id` resolves the stored document: its exact text, tags and outgoing links; then the
  draft path runs unchanged. A draft has no node, so graph seeding uses `declared_links`
  plus nearest-neighbour seeds.

**Output sections** (each item: `{id, doc_id, doc_name, text, score, reason, path}`):

| Section | Construction | Ordering |
|---|---|---|
| `linked` | traversal from the item (or declared link targets) over `depends_on`, `parent_of`, `member_of`, ≤ 2 hops; upstream = outgoing `depends_on`, downstream = incoming | hop, then edge type |
| `peers` | existing hybrid (vector + BM25, RRF) via `query_domain`, filtered by `level:`/domain tags, excluding the item itself and `linked` members; `near_duplicate: true` above a similarity threshold | score |
| `normative` | same hybrid restricted to segments tagged `doctype:legal` or `doctype:standard`, scoped by domain tags; explicit `cites` targets included first | explicit first, then score |
| `coverage` | counts/booleans: `has_upstream`, `has_downstream`, `near_duplicates`, `normative_by_source`, `declared_links_unresolved` | n/a |

- Text is exact (no LLM compression); no LLM is called anywhere in this path.
- Budget: approximate tokens as characters/4; split across sections, trim from the tail of
  each list; report `truncated` per section.
- Initial near-duplicate threshold is 0.90 cosine; a tunable default to be validated on
  the fixture, not a tuned constant.
- `normative_by_source` makes gaps visible ("no `standard` clause matched this
  ASIL-tagged requirement") instead of returning silence.

## 5. Out of scope

- A `StandardsAdapter` parsing clause structure/citations inside SMOS (follow-up slice,
  after real standards have been ingested and clause shapes are known).
- The "is this a good requirement" judgement.
- Live Codebeamer API access, write-back, incremental sync.
- Graph-propagation ranking (5.6), domain classification, coverage thresholds (5.5c proper).
- Segment-level link targets.

## 6. Risks / notes

- `context_builder.py` expansion walks only `references` edges. R2 does its own traversal
  and does not change that file; hybrid retrieval behaviour is untouched.
- Adding the placeholder-upgrade path touches `GraphStore`; it must not change
  `INSERT OR IGNORE` behaviour for existing callers.
- `parent_of` is deliberately not `contains`: `contains` means document structure and is
  walked by `reconstruct()`/re-sync.
- Compressed summaries of this repo's own code are not authoritative (see CLAUDE.md);
  implementation work reads real source before editing.

## 7. Verification

- Unit: link wiring (direction preserved), cross-system target, placeholder create +
  upgrade, re-sync replaces owned edges only, vocabulary validation, each section's
  ordering/budget/`truncated`.
- End-to-end fixture: SYS.1 → SYS.2 → SYS.3 chain, a near-duplicate, a legal document, a
  standard-like document with a `cites` link; assert all four sections for both an
  `item_id` query and a draft.
- Bench: new dimension in `tests/bench_legal_regulations.py` (or a sibling bench) —
  a SYS.2 draft surfaces its SYS.1 parent and cited provision with graph on, not off.
- Full pytest green; existing benches unchanged.

## 8. Sequencing

Plan and build **R1 first** (the graph must be populated), then R2. One spec, two plans or
one plan in two phases — decided at planning time.

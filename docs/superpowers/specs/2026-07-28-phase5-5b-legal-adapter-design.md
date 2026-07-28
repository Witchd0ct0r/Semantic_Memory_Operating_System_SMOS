# Phase 5.5b (slice 1) — Legal Adapter & Reference-Extraction Design

**Status:** Approved by the Governor 2026-07-28 (design walked through in sections,
each approved). Not yet planned — next step is a tree-verified implementation plan
via `writing-plans`, deliberately deferred to a fresh session.

**Phase:** Phase 5.5b, first bounded slice. Parent design:
`docs/superpowers/specs/2026-07-17-domain-general-ingestion-and-retrieval-design.md`
(Pillar F, §8.5). This slice exists because Pillar F's domain classification and
fan-out primitives need a concrete non-markdown adapter to validate against before
they can be built for real (per `docs/HANDOFF.md`'s 5.5b next-actions note) — this
spec delivers that adapter, nothing more.

---

## 1. Why this slice, and why bounded

Pillar F as specced is four sub-items: typed/extensible traceability edges, domain
tagging + classification, fan-out evidence-gathering primitives, and a coverage/gap
signal. Attempting all four in one plan was considered and rejected — Phases 5.1
through 5.5a each shipped one bounded, tree-verified slice at a time, and this slice
follows that discipline instead of bootstrapping four new subsystems against a single
new adapter with nothing proven yet.

This slice's job is narrower and concrete: build `LegalAdapter` (structural grammar +
reference extraction) and prove it closes the specific gap
`tests/bench_legal_regulations.py` already measures — cross-reference graph-rescue on
plain legal prose currently sits at 0% because `smos/memory/entity_extractor.py`'s
backtick-only pattern never fires on unmarked prose (see the `legal-regulations-retrieval-gap`
cross-session memory). Domain classification, typed traceability edges, the fan-out
primitives, and the coverage/gap signal are explicitly deferred to a later sub-phase
(tentatively 5.5c) once this foundation is real.

---

## 2. Architecture

New file `smos/ingest/adapters/legal.py`, implementing the existing `DomainAdapter`
protocol (`smos/ingest/adapters/base.py`) — **no interface changes required**. The
protocol already generalizes across domains; this slice is proof of that, not an
extension of it.

- Registered via the existing `register_adapter()` (`smos/ingest/adapters/registry.py`),
  same mechanism as `MarkdownAdapter`.
- Selected explicitly per ingestion call — `do_ingest_document_schema(payload, store,
  adapter=LegalAdapter())` — there is no auto-detection of adapter from document
  content today, and this slice doesn't add any.
- `edge_types = frozenset({"references"})`. Matches `MarkdownAdapter` exactly. Pillar
  F's typed traceability vocabulary (`satisfies`, `verified_by`, `refines`, etc.)
  stays deferred — this adapter has no traceability edges to contribute yet, only
  cross-reference ones.
- `llm_boundary_prompt()` returns `None` for this slice (no LLM-proposed boundaries;
  the structural regex grammar below is expected to cover the common cases).
- **No changes to `smos/memory/entity_extractor.py`.** The fix lives entirely in the
  adapter's own `extract_references()`, following exactly the pattern 5.5a used for
  markdown. The generic backtick/AST extractor (`_update_graph()` in
  `vector_store.py`) keeps running on every stored segment regardless of domain —
  it contributes nothing on legal prose today and continues to contribute nothing;
  that's pre-existing behavior, not a regression this slice introduces.

---

## 3. Structural grammar — `structural_boundaries(text)`

Regex over top-level provision markers: `Section \d+(\.\d+)*`, `Article \d+`,
`Rule \d+(-[A-Z])?(\.\d+)*`, `§\s*\d+`. Each match starts a new span running to the
start of the next match (or end of text) — the same "boundary-to-next-boundary" shape
`MarkdownAdapter.structural_boundaries()` uses for ATX headings.

Sub-clause lettering/numbering (`(a)(1)(i)`) is **not** separately spanned by the
adapter. It falls through to the segmenter's semantic/size-fit cascade layers (§5 of
the parent design), exactly as markdown only structurally splits at heading level, not
at nested list items. This keeps the adapter thin and consistent with the existing
reference implementation's granularity.

---

## 4. Reference extraction — `extract_references(text)`

Given a leaf's lookback window (the existing 300-char `_REFERENCE_LOOKBACK_CHARS`
mechanism in `smos/ingest/segmenter.py`, unchanged), the adapter emits two
independent kinds of triples, merged into one return list:

**a) Citation cross-references.** Reuse the same provision-marker regex from §3. Any
marker match in the window *other than* the window's own nearest/enclosing provision
becomes a `(nearest_provision, cited_provision, "references")` triple — e.g. text
containing "as defined in Section 3.1" links the current provision to Section 3.1.

**b) Defined-term matching (heuristic phrase matching).** Two regexes, no
document-wide term registry:

- A *defining-pattern* regex — `"?([A-Z][A-Za-z]+(?:\s+[A-Z][a-z]+){1,3})"?\s+(?:means|shall mean)\b`
  — detects a segment that *defines* a term and emits a self-topic triple
  `(term, term, "references")`. This mirrors `MarkdownAdapter`'s heading self-topic
  trick exactly (a heading names its own section; here, a defining sentence names its
  own term).
- A general *Title-Case-phrase* regex (2-4 consecutive capitalized words) scans every
  window for occurrences of phrase-shaped text and emits
  `(nearest_provision, phrase, "references")` for each. A segment that merely *uses* a
  term (without defining it) still resolves to the same `concept:{name}` node the
  defining segment created — purely because both triples lowercase to the same
  concept id (`concept:covered institution`, etc.), not because either segment knows
  about the other. This is why no whole-document pass or term registry is needed: the
  graph, not the extractor, is where the terms get reconciled.

**Accepted false-positive risk:** the general Title-Case scan fires on any capitalized
multi-word phrase, not only genuine defined terms — a proper noun or a
sentence-initial capitalized phrase will also produce a `references` edge. This is
additive noise in the graph (extra low-value concept nodes), not a correctness bug —
it never corrupts a real cross-reference, it only adds ones nobody will query for.
Explicitly accepted for this slice; not scheduled to be solved now.

---

## 5. Testing & acceptance

**New pytest-collectible test** (`tests/test_legal_adapter.py`): runs a small
legal-shaped document through `do_ingest_document_schema()` with `LegalAdapter()`,
then verifies graph-rescue via `context_builder.py`'s existing 2-hop expansion — a
query for a term used only in a "target" segment must surface it via the "anchor"
(defining) segment's concept edge. **No changes to `context_builder.py`** — same
proof shape 5.5a used for markdown.

**New comparison arm in `tests/bench_legal_regulations.py`** (existing standalone
script, not pytest-collected — see its own docstring). Alongside the current
"graph off / graph on, plain prose vs backtick-marked" runs, add a third condition:
ingest via `LegalAdapter` through the schema-driven path instead of raw `store()`.
This directly demonstrates plain-prose cross-reference recall moving off 0% without
requiring the backtick convention the script's docstring calls out as not existing
for legal text. The existing citation/defined-term recall dimension (BM25/vector-only,
already 100%) is unaffected and untouched.

---

## 6. Non-goals (explicitly deferred, unchanged from scope decision)

- Domain classification (adapter rules + LLM fallback).
- Typed traceability edge vocabulary (`satisfies`/`verified_by`/`refines`/`allocated_to`/
  `complies_with`/`impacts`).
- Fan-out evidence-gathering primitives (`resolve`/`traverse`/`classify_domains`/
  per-domain `recall`/`coverage_check`).
- Coverage/gap signal.
- Multilingual embeddings (separate, optional, cross-cutting per parent spec §9).
- PDF/DOCX/HTML client transformers (Pillar G, parent spec §8.6) — legal text is
  assumed to arrive as plain/markdown-shaped text via the existing schema-ingestion
  path, the same content shape `MarkdownAdapter` already consumes. No new client
  transformer is built in this slice.

All of the above remain queued under Phase 5.5b/5.5c in `docs/HANDOFF.md`'s sub-phase
table, to be scoped in a future design once this adapter is real and merged.

---

## Revision log

- **2026-07-28** — Initial design, brainstormed and approved in sections (scope,
  reference-extraction mechanics, testing/acceptance). Governor selected: legal
  adapter as the concrete non-markdown adapter (with intent to broaden to other
  adapters, e.g. requirements, later); bounded first slice (adapter + reference
  extraction only, Pillar F's other three sub-items deferred); heuristic phrase
  matching (not a two-pass corpus-wide term registry) for defined-term
  cross-referencing.

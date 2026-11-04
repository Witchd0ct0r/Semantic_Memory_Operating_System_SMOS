# Scoped retrieval, discovery and hit enrichment — design

Date: 2026-11-04. Status: draft for the Governor's approval.
Origin: the issue ledger of an external parser / query-agent pipeline (SMOS-001..012). This design covers
SMOS-001, -002, -003, -004 (partly), -010 (no), -011. The tool-user agent reviewed the intent and its
feedback is built in.

## Intent

A consumer that holds a large multi-norm corpus must be able to (1) restrict a query to a tag scope and
trust that nothing outside it comes back, (2) ask what is stored and whether a result is complete, and
(3) map a hit exactly back into the document text. Today none of the three holds, and the consumer carries
a local manifest and client-side filtering to compensate (3 hours of avoidable downstream model calls were
measured on one run, because an "unscoped scoped" query returned the whole corpus).

## Goals / non-goals / anti-goals

Goals: real tag pre-filtering; listing of documents and tags; completeness signal (`total`); paging;
always-numeric score; exact, testable offsets and a version marker.

Non-goals (parked, with reason): multilingual embeddings (SMOS-005, no evidence of harm yet, needs a new
bundled model and a re-embed); document delete / stable segment ids (SMOS-006, softened by the no-op
re-send in Part D); batch query (SMOS-010); Retry-After / quarantine status codes (SMOS-007); a `group`
key to model multi-block clauses; concurrency (008).

Anti-goals: a scoped query that returns fewer than `k` hits although enough in-scope items exist; a filter
that is silently stale; any silent clamp of a request parameter.

## Release shape

Version 0.5.0 (new endpoints and one behaviour change; my call). Three commit groups, A first and separate.
Release notes name A as the only behaviour change: before, `tags` leaked out-of-scope hits; now it never does.

## Part A — tag scoping

**Parameters** (`/query`, and `/recall` for the hybrid and repo strategies):
`tags` = any-of (existing meaning), `tags_all` = all-of, `tags_none` = none-of. The three combine with AND.
Matching is exact and case-sensitive (tags contain colons, e.g. `norm:MBST`; no wildcards).
`recall component=X` is `tags=[X]` and goes through the same path.

**Entity.** A tag belongs to the stored memory it is written on. Segments copy their document's
`domain_tags` plus `doctype:<type>` at ingest (`ingest_tools.py`), so a segment hit is filtered by its own
tags, which equal its document's. A document's tag change takes effect only through re-ingest, which
replaces its segments, so nothing can drift between a document and its segments.

**Pre-filter, not post-filter.** Eligibility is computed from an index table before ranking:
- New store migration 4: table `memory_tags(row_id INTEGER, tag TEXT, PRIMARY KEY(tag,row_id)) WITHOUT ROWID`,
  plus an index on `row_id`. `row_id` is `memories.row_id`, the same id FAISS uses.
- The migration **backfills** from `memories.tags` (split on `,`, as `contract.py` forbids `,` inside a tag).
- The table is written in `store`, `store_batch` and removed in `delete`, `delete_batch` — the only four
  places that insert or remove memories rows — inside the same transaction as the row itself. A test
  asserts `memory_tags` equals the split of `memories.tags` after ingest, re-ingest, delete and a
  backup load (the table lives in `metadata.db`, so a load restores it consistently).
- Vector leg: the index is `IndexIDMap(IndexFlatIP)` (exact). Search is restricted to the eligible
  `row_id`s (a FAISS id selector, or the equivalent exact method the builder prefers), so the top-`k`
  is taken from in-scope items only. A narrow tag still returns `k` hits when `k` in-scope items exist.
- Keyword leg: BM25 candidates are intersected with the eligible set before fusion.
- Graph expansion: the final result set is filtered, so a scoped query never returns an out-of-scope node.
- **No fallback.** The "retry unfiltered when nothing matched" path in `build_context` is removed.

**Unknown tags.** `unknown_tags` in the response lists the names in `tags` and `tags_all` that have zero
items in `memory_tags` (a typo, or a norm not ingested). Names in `tags_none` are not reported (excluding
nothing is correct). With `tags_all`, one unknown tag guarantees an empty result, and the field says why.

## Part B — discovery, totals, paging

- `GET /documents` — one row per document: `id` (doc_id), `external_handle {system, native_id}`, `doc_type`,
  `tags` (domain_tags + `doctype:`), `segment_count`, `updated_at` (from the ingest checkpoint),
  `document_hash`. Filters: `system`, `tag` (repeatable, all-of), `limit` (default 100, max 500), `offset`.
  Sorted by `id`, so paging is deterministic. Read route.
- `GET /tags` — `[{tag, count}]` sorted by tag; `count` is the number of stored memories carrying it.
  Optional `prefix` (e.g. `norm:`) and `limit`/`offset`.
- **`total`** on `/query` and `/recall` (hybrid, repo): the number of stored memories eligible under the
  filter, in the same unit as the hits (segments and memories, not documents). Without a filter it is the
  index size. The retrieval path has no minimum-score cutoff and the vector leg always fills `k` from the
  eligible set, so **`total <= k` means the response contains every eligible item**. (If a cutoff is ever
  added, `total` must be redefined; a comment at the cutoff site says so.)
- `offset` on `/query` and `/recall`: applied to the final fused ranking. `offset + k > 1000` is rejected
  with HTTP 400 and a message (never clamped). Graph-neighbour extras are added only when `offset == 0`.
  `k` stays capped at 100.
- Ordering is deterministic: fused score, then `id`, so pages neither skip nor repeat for an unchanged store.
- Read-only tokens: the new routes are on the `read` router; the route-classification tests are extended and
  a test calls them with a `read`-scope token (and expects write routes unchanged). The Governor/consumer
  re-checks with the real token after deployment.

## Part C — hit enrichment and stored blocks

- `score` is always a number and keeps its meaning, cosine similarity (higher is better). A keyword-only hit
  has no vector rank, so its cosine against the query is computed exactly from the stored vector (refined
  from "fused RRF score" during planning, so existing consumers' thresholds keep working). The ranking
  order is still the fused order. Documented (SMOS-003).

**Why blocks are stored.** Today the parser's blocks are only used to steer segmentation and are then
discarded; sections are a by-product (only a block over 800 characters becomes a section, and a block under
20 stripped characters is merged into its predecessor by `_merge_undersized`). Neither gives a consumer a
reliable clause range. The Governor and the tool-user agent chose to store the parser's blocks as given.

**`document_blocks`** (store migration 5): `(doc_id, ordinal, char_start, char_end, block_type,
PRIMARY KEY(doc_id, ordinal))`. Written at ingest from the parser's own blocks, in the same step that clears
the document's old segments (re-ingest replaces them, a delete removes them; the write is idempotent so a
resumed ingest is safe). Block ranges come from the raw payload, **never** from the segmenter, so
`_merge_undersized` cannot alter them for any parser's input. The segmenter itself is unchanged.

**Tiling property (stated invariant, and the strongest acceptance test).** For a document, block ordinals
are contiguous from 0, the first block starts at 0, each block starts where the previous ends, and the last
ends at the text length. Therefore `"".join(text[b.start:b.end] for b in blocks) == text`, where `text` is
exactly what `/nodes/{doc_id}/text` returns. It holds by construction of `blocks_to_full_text`; a test proves
it on a real ingest and a startup-free consistency check can assert it per document.

**What a segment hit carries:** `doc_id`, `ordinal`, `char_start`, `char_end` (already present),
`block_ordinal`, `block_char_start`, `block_char_end`, `block_type`, and `document_hash`.
- `block_ordinal` is the block that contains the segment's `char_start`. A segment can span more than one
  block only when the segmenter merged a short block into its neighbour; the hit then also carries
  `block_ordinal_end` (the block containing `char_end - 1`; equal to `block_ordinal` otherwise).
- A clause that a parser split across several blocks (a "<head> (cont.)" sequence) is **not** modelled by
  SMOS: the consumer stitches consecutive blocks itself. A pass-through `group` key on blocks would make
  that cleaner but is not part of this release (parked).
- Documents ingested before 0.5.0 have no stored blocks: their block fields are `null` until the document
  is re-sent (see Part D). Consumers must treat `null` as "unknown".
- The old internal `section_id` is not exposed on hits. Sections remain a segmenter by-product.

**Offset unit and frame.** Offsets are Unicode code points (Python `str` indices) into the document's full
text, which is exactly the concatenation of the `block.text` values the parser sent; SMOS adds no separator.
`/nodes/{doc_id}/text` returns that text and adds `document_hash` and `char_length`; its `complete` flag
now also requires the first segment to start at 0 and the last to end at `char_length`.
`/nodes/{doc_id}/text?include=blocks` (default off) adds
`blocks: [{ordinal, char_start, char_end, block_type}]`.

`document_hash` is the ingest content hash already stored in `ingest_checkpoints` (available for old
documents immediately). A consumer compares it to detect stale offsets after a re-ingest. No stable segment
ids are promised; ordinals and ranges are valid within one `document_hash` only.

**Alignment test (acceptance).** An ingest fixture with: repeated heading numbers, German umlauts (non-ASCII
before the clause), a clause split into "(cont.)" blocks, blocks ending in `\n`, and a block under 20
characters. Checks: the tiling property above; for every segment `text[char_start:char_end] == content`;
for every hit `block_char_start <= char_start` and `char_end <= block_char_end` (or the `_end` block);
a short parser block keeps its own stored range although its segment was merged.

## Part D — re-sending existing documents (migration path)

- Store migrations 4 (`memory_tags`, with backfill) and 5 (`document_blocks`, empty) run at startup in one
  transaction after a `metadata.db.bak-v3` backup. The FAISS index is untouched; nothing is re-embedded.
  Loading a pre-0.5.0 backup migrates it the same way (the loader already runs the store migrations).
- Tag scoping, `/documents`, `/tags`, `total`, scores and `document_hash` work on old data at once. Only the
  block fields are `null` for a document until it is re-sent.
- **No-op re-send fast path (new; my call, flag if you disagree).** Today, re-sending a complete document
  always deletes and recreates its segments, so segment ids, section/segment summaries and graph links to
  them change (SMOS-006) even when nothing changed. New rule: if the document is `complete`, the new
  `content_hash` equals the stored one, and tags and `doc_type` are unchanged, then the ingest only writes
  `document_blocks`, refreshes the document node metadata and source links and applies the document
  summary as today; **segments and their ids are kept**. A changed hash or changed tags takes the existing
  replace path. This makes the one-off block backfill cheap and id-stable, and makes accidental re-sends
  harmless. The result reports `segments_kept: true`.
- Known gotcha, unchanged: a re-send without `summary` deletes the document's summary. Documented in the
  guide and in the release notes. A replace path still orphans segment-level summaries and any memory
  `source_ids` that pointed at old segment ids.
- Time cost to measure before deploying to a large store: the migration-4 backfill (one pass over
  `memories`). The Governor runs it on a copy of the store.

## Constraints

- Auth stays as is: every `/api/v1` route except `/health` depends on `require_token`
  (`test_every_route_in_schema_is_protected`); new write endpoints: none. `tests/test_api_scopes.py` is updated.
- New table = new numbered migration; no edit of an existing one. Backup is taken by the runner as today.
- Pentest mode and backup/load already copy `metadata.db`; the new table needs no special handling, but the
  verification covers a load of an old (pre-0.5.0) backup (migration + backfill on load).
- Docs: `docs/API.md`, the parser/agent guide in `docs/guide/`, OpenAPI descriptions (fix the `tags` text),
  a runbook note, and a short mention in the release notes.

## Left to the builder

Exact FAISS selector mechanism; migration batching for a store with many rows; response field naming where
this document is silent; test structure. Settled by me (flag if you disagree): 0.5.0; offset cap 1000 with a
400; `unknown_tags` ignores `tags_none`; no `group` key on blocks; the no-op re-send fast path (Part D).
Decided by the Governor: store the parser's blocks (`document_blocks`).

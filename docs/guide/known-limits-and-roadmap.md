# Known limits and roadmap

Behaviors of SMOS 0.3.x that surprise people, each with what to do today. **Status** is `technical debt` (SMOS should change) or
`by design` (it stays, the guide explains it) or `future` (a planned or possible feature, not scheduled). Every item here was
checked against the code or a real run (see [../guide-verification.md](../guide-verification.md)); the ones marked *pinned* have a
test that fails if the behavior changes, which is the signal to update this page.

If you hit something that looks like a bug and is not listed here, report it: [troubleshooting-and-reporting.md](troubleshooting-and-reporting.md).

## Adapter selection

**Today:** `POST /api/v1/documents` runs ingestion with no domain adapter. SMOS therefore does **not** detect cross-references in
text ("see clause 5.2", "ISO 26262-6"). The markdown and requirements adapters are registered but never selected over the API, and the
legal adapter (citation detection for legal text) is not registered at all.
**Do now:** read the references yourself in the parser and declare them in `source_links`
([parser-author-guide.md](parser-author-guide.md#links)).
**Status:** technical debt. **Direction:** select an adapter from `doc_type` or an explicit request field, and register the legal
adapter.

## "Normative" means `doctype:legal` or `doctype:standard`

**Today:** the *normative* section of `/requirement-context` is hard-wired to segments tagged `doctype:legal` or `doctype:standard`
(from your `doc_type`). Guidance such as process documents or internal policies is not treated as normative unless you pass
`normative_tags` yourself. Parent membership is not used to decide domain membership.
**Do now:** set `doc_type` to `standard` or `legal` for normative sources, or pass `normative_tags` on each call.
**Status:** technical debt. **Direction:** configurable normative scopes and a `guidance` document type.

## Tag scopes are a real filter (since 0.5.0)

**Today:** `tags` (any-of), `tags_all` and `tags_none` on `/query` and `/recall` are applied before ranking, to the vector, keyword and
graph parts alike. Nothing outside the scope is returned, and a scope that matches nothing returns an empty list with `unknown_tags`
naming the tags no item carries. Before 0.5.0 the scope was only a preference (an unfiltered fallback and an unfiltered keyword part), so
a client that filtered on `metadata.tags` itself keeps working and can drop that code.
**Do now:** use `total` to know whether a response is complete (`total <= k`), and page with `offset` (`offset + k` at most 1000).
**Limits:** matching is exact and case-sensitive; there are no wildcards. Deep paging is capped at 1000 results on purpose.
**Status:** resolved in 0.5.0.

## A query always returns its nearest items

**Today:** results are the top `k` nearest segments, however weak. A nonsense query returned three results with `mode: "extractive"` and
a `confidence` of about 0.54; `mode: "uncertain"` appears only when nothing was retrieved at all.
**Do now:** judge `score` (higher is closer; the real run showed about 0.75 to 0.81 for a direct hit and 0.02 to 0.18 for unrelated
text) and apply your own threshold; quote text only when it answers the question.
**Status:** by design for now. **Direction:** an optional minimum score.

## Results are top-k, and links are not separated from similarity

**Today:** a result list does not say whether an item was reached through an explicit link or found by similarity only.
`/requirement-context` does separate `linked`, `peers` and `normative`; `/query` does not.
**Do now:** use `/graph/query` from a `doc_id` to see declared links, and `/requirement-context` for evidence grouped by reason.
**Status:** future.

## Segments follow blocks and size, not headings

**Today:** blocks under 20 characters merge into the previous segment (a tiny FIRST block merges into the next one), blocks over 800 characters are split, and `block_type` /
`heading_level` are not used for cutting. Sections exist only for split blocks. *Pinned.*
**Do now:** one block per clause with its heading ([parser-author-guide.md](parser-author-guide.md#how-blocks-become-segments)).
**Status:** by design; **future:** use headings to build sections and to keep a heading with its body automatically.

## Accepted but ignored fields

**Today:** these ingestion fields are accepted and validated for type, then dropped: `external_handle.source_updated_at`, block `source_anchor`, `schema_version`, `confidence`, `transformer_name`, `transformer_version`,
`ingested_at`.
**Do now:** keep such data in `metadata{}`; it is stored on the document node and returned by `/graph/query`.
**Status:** technical debt (store and return them, or stop accepting them).

## Quarantined payloads cannot be read back

**Today:** a quarantined payload is kept under its `quarantine_key`, but no endpoint lists or returns it.
**Do now:** keep your own copy of what you send; fix and resend.
**Status:** future (a read endpoint and a retry).

## No delete or list of documents

**Today:** there is no API to delete a document or to list stored documents. `DELETE /memories/{id}` removes one memory or
segment; re-ingesting a document replaces all of its segments.
**Do now:** re-ingest with the changed content; ask the admin if a document must disappear.
**Status:** future.

## Links are wired when the source is ingested

**Today:** a link is an edge from the source document, replaced every time the source is re-ingested; a target that does not exist yet
becomes a placeholder node that the real ingest fills in later. *Pinned.* Traversal in `/graph/query` ignores edge direction, so a
`contains` query from a section also returns its parent.
**Status:** by design.

## Smaller items

- The interactive API page (`/api/v1/docs`) loads Swagger assets from a CDN; offline, read `/api/v1/openapi.json` directly (needs a token).
- A re-ingest that changes the text or the tags issues new segment ids (an identical re-send keeps them, see
  [re-ingesting](parser-author-guide.md#re-ingesting)); do not store segment ids across changed re-ingests, store `doc_id` plus
  `document_hash` plus offsets or a quoted sentence.
- Large stores: search is exact (not approximate) and fast at the sizes expected in a trial; see [../hnsw-assessment.md](../hnsw-assessment.md).
- These docs are files in the repository. **Future:** SMOS will serve them through the API so an agent with only a URL and a token can
  read them.
- **Future:** a tickets API (token holders post bug reports and feature requests) and an admin panel to review them; the
  templates in [troubleshooting-and-reporting.md](troubleshooting-and-reporting.md#future) are shaped for it.

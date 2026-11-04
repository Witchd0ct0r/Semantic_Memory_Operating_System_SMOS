# Scoped retrieval, listings and stored blocks: verification record (release 0.5.0, unreleased)

Design `docs/superpowers/specs/2026-11-04-scoped-retrieval-and-discovery-design.md`, plan
`docs/superpowers/plans/2026-11-04-scoped-retrieval-and-discovery.md`, runbook `docs/runbooks/scoped-retrieval.md`.
Origin: an external parser and query-agent pipeline reported SMOS-001 (tags did not filter), -002 (no listing, no total, 100-hit cap),
-003 (null score), -004 and -011 (no block structure, no section/clause range). Built test-first, inline, one commit per task.

## 1. What was wrong (SMOS-001, from the code)

Three separate causes: the vector search took the nearest `k` of the whole corpus and dropped out-of-scope hits afterwards, so a narrow
tag returned few or none; a tag filter that found nothing silently repeated the search without it; the keyword (BM25) part and the
graph expansion were never filtered. The tool user measured 64 of 100 hits in scope for `norm:MBST`.

## 2. Automated tests

Full suite `python -m pytest tests -q`: see section 4 for the final count. New files: `test_memory_tags.py`, `test_tag_scoping.py`,
`test_api_scoping.py`, `test_discovery_api.py`, `test_document_blocks.py`, `test_hit_enrichment.py`, `test_block_alignment.py`,
`test_resend_fast_path.py`; a pre-0.5.0 backup test in `test_backup_load.py`. The SMOS-001 regression is
`test_scoped_query_never_returns_out_of_scope`. The acceptance test `test_block_alignment.py` was checked to fail when the stored
block offsets are shifted by one character, and it also showed that the segmenter leaves no gaps (segments tile the text).

Two existing tests pinned the old behaviour and were changed: `test_query_tags_are_a_preference_not_a_filter` became
`test_query_tags_are_a_filter_not_a_preference`, and the benchmark's `timed_context` mirror got the id tie-break.

## 3. Real run against a live server on a 0.4.0 store

The previous release's code (`git archive 4bad22e`) seeded a store (schema 3: 12 documents, 24 segments, 27 items, three norms, three
plain notes). The new code was then started as a real `python -m smos.api.main` process on that store, driven over HTTP with a read/write
and a read-only token. **28 of 28 checks passed** (`docs/runbooks/scripts/e2e_scoped_retrieval.py`):

- opening the 0.4.0 store migrates it to schema 5 and writes `metadata.db.bak-v3` first (0.02 s for 27 rows on the copy)
- `/health` reports 0.5.0; a query scoped to `norm:A` returns 11 hits, all carrying `norm:A`, and `total` is 11
- `tags_none` leaves only the 3 notes; an unknown tag gives an empty list with `unknown_tags` (no fallback); disjoint `tags_all` is empty
- pages of 4 at offsets 0, 4, 8 equal one page of 12 with no repeats; `offset + k > 1000` is HTTP 400; every hit of `k=100` has a numeric score
- `GET /documents` lists 12 and filters by system and two tags (4); `GET /tags` counts per norm
- an old document's hit has `null` block fields and a `document_hash`; its text is complete with a correct `char_length`
- the read-only token can call `GET /documents`, `GET /tags` and a scoped query, and still gets 403 on a write
- an identical re-send answers `segments_kept: true` with the same segment ids, the blocks appear and tile the text exactly, the
  document hash is unchanged, a hit maps into the text and onto its block (German text with umlauts), the document summary is kept
- a re-send without `summary` deletes the document summary (documented); changed tags replace the segments and the tag index follows

The server was stopped afterwards; its temporary data lives outside the repo.

## 4. Decisions made during the build

- `score` stays cosine similarity for every hit (keyword-only hits get their cosine computed from the stored vector); the design first
  said "fused RRF score", which would have changed the values existing clients threshold on.
- The rest are in the plan's ledger; they are summarised in the session handoff.

## 5. Not done, on purpose

Multilingual embeddings (SMOS-005; no evidence of harm yet), document delete and stable segment ids (SMOS-006; softened by the no-op
re-send), batch query (010), Retry-After and quarantine status codes (007), a `group` key for multi-block clauses.
**Pending on the Governor:** time the migration on a copy of the Debian store, update, re-send documents to backfill blocks
(`docs/runbooks/scoped-retrieval.md`), and have the tool-user agent re-run its alignment check with its read-only token.

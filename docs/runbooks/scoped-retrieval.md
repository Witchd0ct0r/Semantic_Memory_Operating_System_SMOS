# Scoped retrieval, listings and stored blocks (0.5.0)

What changed for operators and how to roll it out. Design: `docs/superpowers/specs/2026-11-04-scoped-retrieval-and-discovery-design.md`.
Verification record: `docs/scoped-retrieval-verification.md`.

## Release notes (paste into the GitHub Release)

**Behaviour change (the only one): tag scopes are real filters.** `tags` on `/query` and `/recall` used to be a preference: an
unfiltered fallback and an unfiltered keyword part let out-of-scope items through. Now nothing outside the scope is returned, and a
scope that matches nothing returns an empty list with `unknown_tags`. Clients that filter on `metadata.tags` themselves keep working.

New: `tags_all`, `tags_none`, `offset` (offset + k at most 1000, otherwise HTTP 400), `total` (eligible items; `total <= k` means the
response is complete), `unknown_tags`; always-numeric `score`; `GET /documents`; `GET /tags`; every segment hit carries `document_hash`
and the parser block it came from (`block_*`); `GET /nodes/{id}/text` returns `document_hash`, `char_length` and `?include=blocks`;
an identical re-send keeps segment ids.

No rebuild of the image is needed (no new dependency, no new file type). Two store migrations run at startup (4 `memory_tags`,
5 `document_blocks`), after a `metadata.db.bak-v3` copy. Block fields are `null` for documents ingested before 0.5.0 until they are
sent again. A re-send without `summary` deletes the document summary (unchanged behaviour, now documented).

## Rolling out

1. **Time the migration on a copy.** Copy `metadata.db` (and its `-wal`) to a scratch folder and open it once with the new code, or
   run the update on a test installation. Migration 4 reads every row of `memories` once. Expect seconds per hundred thousand
   segments; measure before updating a large store. Do not record company figures in the repo.
2. **Update** from the dashboard as usual. The updater snapshots the data, so a failed health check restores it.
3. **Backfill blocks.** Re-send each document unchanged (the payload is the same as before). The text, tags and `doc_type` match, so
   SMOS keeps the segments and their ids, summaries and links and only writes the blocks (`"segments_kept": true` in the response).
   **Include `summary` in the re-send** or the document summary is deleted. A document whose tags or text changed is replaced as before.
4. **Check** with the read-only token: `GET /api/v1/tags`, `GET /api/v1/documents?limit=5`, and one scoped query (`total`, no
   foreign tags in `metadata.tags`).

## Recipes

- *Is a result complete?* `total <= k`.
- *Typo in a tag?* `unknown_tags` names it; `GET /api/v1/tags?prefix=norm:` lists the real ones.
- *Stale offsets after a re-send?* Compare `metadata.document_hash` with the one you stored.
- *Segment ids changed?* The text or the tags changed, so the segments were replaced (documented). Use `doc_id` + `document_hash` +
  offsets as the address.

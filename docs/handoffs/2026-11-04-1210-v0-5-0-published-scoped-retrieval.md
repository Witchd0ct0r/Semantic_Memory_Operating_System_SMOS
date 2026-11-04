# Handoff — v0.5.0 published (scoped retrieval, listings, stored blocks) — 2026-11-04 1210

## State
Release **v0.5.0 is published** (tag and GitHub Release on commit `eeeb823`, `origin/main` equal to local `main`, clean tree). It was built from an
external parser + query-agent pipeline's issue ledger (SMOS-001..012). Verified here: full suite **1194 passed**; a real `python -m smos.api.main`
process on a store created by the 0.4.0 code passed **28/28** checks (migration to schema 5, scoping, paging, listings, read-only token, re-send
keeping segment ids, block tiling). **Not verified:** migration time on the real (Debian) store, the update through the dashboard, and the other
project's own alignment check with its read-only token. No server or background process is running (the verification server was stopped; port
18765 answers nothing).

## Completed this session
- Tag scoping is a real pre-filter (`memory_tags`, store migration 4, backfilled; delete trigger; one insert helper), no unfiltered fallback,
  keyword and graph parts scoped too. `tags_all`, `tags_none`, `offset` (offset+k ≤ 1000 else 400), `total`, `unknown_tags`, numeric `score` (cosine).
- `GET /documents`, `GET /tags` (read routes, work with read-only tokens).
- `document_blocks` (migration 5): parser blocks stored at ingest; every hit carries `document_hash` and `block_*`; `/nodes/{id}/text` adds
  `document_hash`, `char_length`, `?include=blocks`; `complete` also requires the first segment at 0.
- Identical re-send keeps segments and ids (`segments_kept`); a re-send without `summary` deletes the document summary (documented).
- Final review found and fixed a per-query cost (resolved tag scope cached until the store changes; 66-109 ms at 100k segments measured).
- Docs: query guide, parser guide, known limits, API.md, ARCHITECTURE.md, runbook with the release notes, verification record. Memory note added.
- Commits `4bad22e..eeeb823` (18); design/plan committed first (`471fcd0`).

## Next steps (in order)
1. On Debian: copy `metadata.db` (+ `-wal`) and time the migration on the copy (`docs/runbooks/scoped-retrieval.md`, "Rolling out" step 1).
2. Update from the dashboard (no image rebuild needed); then check `GET /api/v1/tags`, `GET /api/v1/documents?limit=5` and one scoped query with the read-only token.
3. Tell the tool-user project to re-send its documents unchanged, **including `summary`**, to backfill blocks (segment ids and summaries stay).
4. Ask that project to re-run its alignment check (hit `char_start:char_end` vs `/nodes/{id}/text`, block tiling) with its read-only token and report.
5. Pick the next project; candidates from the ledger are in "Open items".

## Open items / blockers
- Pending on the Governor: steps 1-4 above (Debian access is the Governor's).
- Deferred minors: unused `_BLOCK_KEYS` in `smos/compression/context_builder.py`; `recall strategy=exact` and `requirement-context` hits lack
  `document_hash`/`block_*`; a keyword-only hit embeds the query twice (`cosine_scores`); unscoped deep paging may repeat a graph extra from page 1.
- Parked ledger items: multilingual embeddings (SMOS-005, MiniLM is English-only; needs evidence of harm first), document delete / stable ids (006),
  batch query (010), Retry-After / quarantine status (007), a `group` key for multi-block clauses, concurrency docs (008).
- Optimization ideas from session 10 (embedding cache, embed outside the lock) are still on hold.

## Key decisions & context
- Tag semantics: `tags` any-of, `tags_all`, `tags_none`, ANDed; exact and case-sensitive; unknown tags only reported for `tags`/`tags_all`.
- `total` counts eligible items in the hit unit (segments/memories); `total <= k` means complete (no score cutoff exists).
- Blocks are stored (table) rather than recomputed because the segmenter merges blocks under 20 characters into a neighbour and makes sections
  only for blocks over 800 characters; hits therefore also report `block_ordinal_end`. Offsets are Unicode code points; the text is exactly the
  concatenation of the sent `block.text` values and segments tile it (tested).
- `score` stays cosine similarity (keyword-only hits get their cosine computed) so existing thresholds keep working.
- Two old tests pinned the leaky behaviour and were replaced; the bench mirror `timed_context` got the id tie-break.
- Work was done inline on `main` (project rule); no reviewer subagent (standing preference).
- Benchmark/company data stay out of the repo.

## Files that matter
- `docs/superpowers/specs/2026-11-04-scoped-retrieval-and-discovery-design.md` and `docs/superpowers/plans/2026-11-04-scoped-retrieval-and-discovery.md` — design and plan.
- `docs/scoped-retrieval-verification.md` — evidence (tests, real-server run, review finding).
- `docs/runbooks/scoped-retrieval.md` — release notes, rollout steps, recipes; `docs/runbooks/scripts/e2e_scoped_retrieval*.py` — the upgrade-path check (seed with the previous release's code, run the new server).
- `docs/HANDOFF.md` — current-state page (session 12 section at the top).
- `smos/memory/vector_store.py` (tag index, scope cache, blocks), `smos/compression/context_builder.py` (`build_context`, hit enrichment), `smos/tools/ingest_tools.py` (blocks write, re-send fast path), `smos/storage/migrations.py` (versions 4 and 5).

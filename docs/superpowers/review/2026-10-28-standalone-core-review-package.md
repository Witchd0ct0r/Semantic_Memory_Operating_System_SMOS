# Review package: SMOS Standalone, Project 1 (Core)

You are reviewing a finished, tested change set. Find real defects: security holes, data-loss
risks, wrong behavior, and gaps between the design and the code. Style nits are not wanted.

## 1. What to review

- Repo: this repository, branch `main` (local; nothing pushed).
- Range: `git diff e903ff7..82c1a5c` (9 commits, 92 files, +3,500 / -9,175; most deletions are
  the removed AI, MCP server, installer and old tests).
- Commits, oldest first: `e711b4f` docs (design, plan) · `a61d011` summaries table ·
  `b1f834e` remove built-in AI · `ab05e81` migrations · `cb9349e` admin db, tokens,
  invitations, usage · `65c5e56` HTTP API · `e2b643b` admin pages · `cc1d71b` schema-leak fix ·
  `0556222` retire MCP, packaging, docs · `82c1a5c` document summary on segment hits.
- Read in this order: the spec, then the plan, then the code.
  - Spec (binding): `docs/superpowers/specs/2026-10-28-standalone-core-design.md`
  - Plan: `docs/superpowers/plans/2026-10-28-standalone-core.md` (its "Review Focus" section)
  - Verification record: `docs/standalone-core-verification.md`

## 2. What the change is

SMOS is a self-hosted store with exact-text retrieval (FAISS + BM25 + typed graph in SQLite).
It was an MCP plugin that called a local LLM. It is now an HTTP service:

1. No code path calls a generative model. Summaries are supplied by callers and stored.
2. FastAPI app (`smos/api`) under `/api/v1`, bearer-token auth on every route except `/health`.
3. Tokens are issued only through single-use invitation links the user claims themselves
   (the admin must never see a token). Admin pages at `/admin/*`, public claim page `/claim/*`.
4. Per-token usage log; versioned, forward-only storage migrations with backup.

Key files: `smos/api/{app,deps,routes,models}.py`, `smos/admin/{db,accounts,tokens,invitations,usage,pages}.py`
(+ `templates/`), `smos/storage/migrations.py`, `smos/memory/vector_store.py`,
`smos/compression/context_builder.py`, `smos/tools/agent_primitives.py`,
`smos/tools/ingest_tools.py`, `smos/ingest/contract.py`.

## 3. Run it

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q          # expect 390 passed, about 30 s
# if a bare `pytest` says "No tests collected" (local hook quirk): rtk proxy pytest tests/ -q

SMOS_DATA_DIR=./tmp-data SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD=choose-one SMOS_PORT=18765 smos-server
# admin UI: http://127.0.0.1:18765/admin/login ; OpenAPI: /api/v1/openapi.json (needs token or admin session)
```

The first run downloads the sentence-transformers embedding model. Use `python`, not `python3`
(Windows Store stub on this machine). Stop the server when done.

## 4. Where I most want your attention (design requirements, hardest first)

1. **Token never visible to the admin; invitation is single-use.** `smos/admin/invitations.py::claim`
   must be atomic under concurrent claims; wrong, used, expired and revoked secrets must be
   indistinguishable to the caller (`/claim/*` in `smos/admin/pages.py`). Can a token or secret leak
   via logs, caches, error pages, redirects, referrers, or the usage log?
2. **Auth coverage.** Every `/api/v1` route except `/health` depends on `require_token`
   (`smos/api/app.py` wires it on a router); `/api/v1/openapi.json` and `/api/v1/docs` need a
   token or admin session. Look for a route, method or path form that bypasses this (note FastAPI
   0.139 nests routers lazily, so `app.routes` is not flat).
3. **Admin surface.** Session handling (`SessionMiddleware`, `same_site=strict`), CSRF on admin
   POSTs (login deliberately has none), login/claim throttling (in-memory, per client host),
   `SMOS_TRUST_PROXY` handling of `X-Forwarded-For`, open-redirect or XSS in templates.
4. **Migrations** (`smos/storage/migrations.py`): all pending migrations in one transaction after a
   file backup; newer-than-code schema must refuse to start. Check the Windows/SQLite WAL
   assumptions, the explicit `BEGIN IMMEDIATE`, and what happens on a crash mid-upgrade.
5. **No generated text anywhere.** Retrieval returns exact text plus caller summaries
   (`to_retrieved_context` in `smos/compression/context_builder.py`). Check that summary lookup
   and deletion (`VectorStore.set_summary/get_summaries/delete_summary`, `delete`, `delete_batch`)
   cannot leave stale or cross-linked summaries, including on document re-sync.
6. **Concurrency.** Requests now run in a thread pool against one `VectorStore` and one shared
   admin SQLite connection guarded by `app.state.admin_lock`. Look for torn state or deadlock.
7. **Behavior preserved where it should be.** Hybrid retrieval, graph expansion, exact recall,
   `requirement_context` and the ingestion contract were meant to be unchanged apart from the
   response shape. Compare `build_context` with the old `build_compressed_context` in
   `git show e903ff7:smos/compression/context_builder.py`.
8. **Inputs a person would actually send:** very long or non-UTF-8 text, empty and whitespace-only
   fields, huge `k`, duplicate `external_handle`, ingest payload edge cases, concurrent re-ingest of the
   same document, a label with HTML in the invitation form, a very long `User-Agent`.

## 5. Decisions already made (please do not re-litigate; flag only if harmful)

- No MCP, no stored-data migration (no data exists), development on `main`, execution inline.
- `GET /api/v1/health` is unauthenticated on purpose (container healthchecks, no data).
- Migration is all-or-nothing in one transaction; the backup file is kept but not used for rollback.
- The Swagger page loads assets from a CDN (blank offline); OpenAPI JSON works offline.
- Graph tables are still created by `GraphStore`, not by migration 1. Admin DB uses one connection.
- No extra API-level lock around the store; an 8-thread stress test passes
  (`tests/test_api_concurrency.py`).
- `smos-doc-sync` skill, `ingest_code.py` and four benchmark scripts were deleted because they
  depended on removed code.
- Known minors: uvicorn's access log prints `/claim/<secret>` for opened-but-unclaimed links;
  README and API.md are intentionally short (a full user guide is a later project).

## 6. Out of scope

Docker packaging, the metrics dashboard, self-update and the full documentation (later projects).
The parked requirement-review work (domain scope queries, bundle evidence).

## 7. Evidence already collected

- 390 tests pass. Real-service run with curl over the full invitation flow (health, 401s, login,
  invitation, claim, second use 404, ingest with summary, query, admin list shows claim IP and
  user agent, token appears 0 times in admin pages, revoke gives 401). Details in
  `docs/standalone-core-verification.md`.
- Legal benchmark unchanged (`python tests/bench_legal_regulations.py`: legal-adapter
  cross-reference recall 33% to 100%).
- Adversarial probes passed: OPTIONS/HEAD without token (405), lower-case `bearer`, token only in
  `X-Api-Key` (401), tampered and forged session cookies (303 to login), 5 KB claim secret (404).

## 8. How to report

For each finding give: file and line, one-sentence defect, a concrete failing input or sequence,
and severity (Critical = data loss, auth bypass or token exposure; Important = wrong behavior a
user will hit; Minor = polish). Say explicitly which of the section 4 items you checked and found
sound. Do not edit the code; report only.

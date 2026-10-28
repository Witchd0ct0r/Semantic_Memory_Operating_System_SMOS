# Project 1 (Core) — verification record

Date: 2026-10-28. Plan: `docs/superpowers/plans/2026-10-28-standalone-core.md`.

## 1. Real-service run

The service was started with `smos-server` on a fresh temporary data directory
(`SMOS_PORT=18765`, admin bootstrapped from environment variables) and driven with curl,
no test client. Observed:

| Step | Result |
|---|---|
| `GET /api/v1/health` without token | 200 `{"status":"ok","version":"0.2.0"}` |
| `POST /api/v1/query` without token | 401 |
| `GET /api/v1/openapi.json` without credentials | 401 |
| Admin login | 303 to the admin pages |
| Admin creates an invitation for "alice" | one-time link `/claim/<secret>` shown |
| User opens the link (no cookies) | page with **Create token**, 200 |
| User presses **Create token** | token shown once (`smos_…`, 48 chars) |
| Second use of the link | 404 |
| `POST /api/v1/documents` with `summary` | `status: success`, 2 segments |
| `POST /api/v1/query` | exact segment text; `document_summary` = the parser's summary |
| Admin token list | shows alice, claim IP `127.0.0.1`, user agent `curl-trial/1.0`; the token appears 0 times |
| `/admin/usage` | per-token call counts |
| Revoke the token, then call | 401 |

**Defect found by this run and fixed:** the first pass returned `summary: null` on the
segments because the summary had been attached to the *document*. Segment hits now also carry
`document_summary` / `document_summary_source` (tests `test_segment_hit_carries_its_documents_summary`,
`test_plain_memory_has_no_document_summary`; documented in `docs/API.md`).

The server process was stopped afterwards (nothing listening on 18765).

## 2. Retrieval quality (legal bench)

`python tests/bench_legal_regulations.py`, after the AI removal:

| Dimension | Graph off | Graph on | Before (HANDOFF) |
|---|---|---|---|
| Citation / defined-term recall | 100% | 100% | unchanged |
| Cross-reference, plain prose (generic extractor) | 33% | 33% | unchanged (known gap) |
| Cross-reference, backtick-marked | 33% | 33% | unchanged |
| Cross-reference via `LegalAdapter` (schema-driven) | 33% | **100%** | 33% → 100% |

No change: the AI removal does not affect candidate ranking or graph expansion, which is what
these benches measure.

## 3. Test suite

`pytest tests/ -q`: all passing (see the last line of the project ledger/commit for the exact
count). Runtime dropped from about 6 minutes to about 30 seconds because no test calls a model.
Includes a concurrency check (8 threads × 6 writes+queries) in `tests/test_api_concurrency.py`.

## 4. Not verified here

- Docker, HTTPS/reverse proxy behaviour (`SMOS_TRUST_PROXY`, `SMOS_COOKIE_SECURE`) — Project 2.
- Swagger page rendering offline (assets come from a CDN).
- Load beyond the 8-thread check; the admin DB shares one connection behind a lock.

## 5. Independent review (Codex) and follow-up

Review: `docs/superpowers/review/2026-10-28-standalone-core-review.md` (range `e903ff7..82c1a5c`).
All five findings were checked against the code, reproduced with a failing test, fixed, and
re-tested:

| Finding | Severity | Fix | Test |
|---|---|---|---|
| Invitation secret in the Uvicorn access log | Critical | `RedactClaimSecret` filter on the access handler (`smos/api/logging_config.py`, used by `main.run`); checked live: 0 occurrences of the secret | `tests/test_log_redaction.py` |
| Concurrent re-ingest of one document mixes versions | Important | per-document lock (`VectorStore.document_lock`) around `do_ingest_document_schema`; the race reproduced in round 2 before the fix | `tests/test_ingest_concurrency.py` |
| Section summaries never returned | Important | segment hits carry `section_summary` / `section_summary_source` | `tests/test_retrieval_summaries.py` |
| Re-ingest leaves summaries of removed sections | Important | `delete_document_segments` deletes them | `tests/test_retrieval_summaries.py` |
| Usage-log retention never applied | Important | pruned at startup and then daily (`SMOS_USAGE_RETENTION_DAYS`, default 90) | `tests/test_usage_retention.py` |

Reviewer's "checks that looked sound": auth coverage, admin surface, migrations, no generated
text, retrieval behavior parity, input checks.

## 6. Token scopes (read-only vs read/write)

Added after the Codex review. Live run on a fresh data directory (curl):

| Call | read-only token | read/write token |
|---|---|---|
| `POST /api/v1/documents` | 403 `{"detail":"this token is read-only"}` | 200 |
| `DELETE /api/v1/memories/{id}` | 403 | — |
| `POST /api/v1/query` | 200 | — |
| `GET /api/v1/openapi.json` | 200 | — |

The admin list shows each token's access level, the claim page and token page state it, and the
403s appear in the per-token usage view. Tests: `tests/test_api_scopes.py` (pins the write-operation
list; read token refused on every write operation, not refused on reads; the database count is unchanged
after attempted writes; 401 stays 401), `tests/test_admin_scopes.py` (inheritance, validation, migration 2
keeps existing rows read/write), page tests in `tests/test_admin_pages.py`. Suite: 423 passed.

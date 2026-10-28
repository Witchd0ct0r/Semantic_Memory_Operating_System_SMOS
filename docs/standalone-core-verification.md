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

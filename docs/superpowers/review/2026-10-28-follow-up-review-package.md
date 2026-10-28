# Review package 2: token scopes, and the fixes for your first review

This is the second review of the same project. Your first review covered `e903ff7..82c1a5c`
(`docs/superpowers/review/2026-10-28-standalone-core-review.md`). This package covers what
changed after it. Find real defects (security, data loss, wrong behavior, gaps against the
design). Style nits are not wanted. Report only; do not edit code.

## 1. What to review

- Repo: this repository, branch `main` (local, nothing pushed).
- Range: `git diff 82c1a5c..77165cc` (30 files, +908 / -71; 17 of them under `smos/`).
- Commits:
  - `2ac419d` docs: first review package (no code).
  - `a26cb5c` **fixes for your five findings** (part A below).
  - `77165cc` **new feature: read-only and read/write tokens** (part B below).
- Background reading: the spec `docs/superpowers/specs/2026-10-28-standalone-core-design.md`
  (the "Governor decisions" list includes the new token kinds), `docs/API.md` (auth section),
  and `docs/standalone-core-verification.md` §5 and §6.
- Run it: `pip install -e ".[dev]"` then `python -m pytest tests/ -q` (expect 423 passed, about
  45 s; if a bare `pytest` reports "No tests collected", use `rtk proxy pytest tests/ -q`). Use
  `python`, not `python3`. Start the service with
  `SMOS_DATA_DIR=./tmp SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD=choose-one SMOS_PORT=18765 smos-server`.

## 2. Part A: are the fixes for your findings sound, and did they open new gaps?

| Your finding | Fix | Where | Tests |
|---|---|---|---|
| Critical: invitation secret in the Uvicorn access log | `RedactClaimSecret` filter attached to the `access` handler through a custom `log_config` | `smos/api/logging_config.py`, `smos/api/main.py` | `tests/test_log_redaction.py` |
| Important: concurrent re-ingest of one document mixes versions | per-document `Lock` (`VectorStore.document_lock`); `do_ingest_document_schema` takes it and calls `_ingest_locked` | `smos/memory/vector_store.py`, `smos/tools/ingest_tools.py` | `tests/test_ingest_concurrency.py` |
| Important: section summaries not returned | segment hits carry `section_summary` and `section_summary_source` | `smos/compression/context_builder.py`, `smos/memory/schemas.py` | `tests/test_retrieval_summaries.py` |
| Important: re-ingest leaves summaries of removed sections | `delete_document_segments` deletes them | `smos/tools/ingest_tools.py` | `tests/test_retrieval_summaries.py` |
| Important: usage retention never applied | prune at startup, then at most daily from the request middleware; `SMOS_USAGE_RETENTION_DAYS` (default 90) | `smos/api/app.py` | `tests/test_usage_retention.py` |

Please look for:

1. **Redaction coverage.** The filter rewrites string args of the access-log record. Is the
   secret exposed anywhere else: error or traceback logs, `uvicorn.error`, the `Referer` header
   echoed anywhere, redirects, templates, the usage log, exception messages? I checked a live
   server's access log (0 occurrences). A reverse proxy has its own logs; that is documented, not
   solved.
2. **The ingest lock.** It is an in-process `threading.Lock` per document id, created on demand and
   never freed. Is serializing enough (other writers to the same document: `delete`, `PUT summary`,
   `forget`)? Any deadlock or lock-ordering problem with `VectorStore._lock`? Unbounded growth of
   the lock dict? It does not work across multiple server processes (the service runs one).
3. **Section summary lookup.** `_section_id` makes one graph query per segment hit. Correct for
   nested sections? Wrong results when a segment sits directly under the document? Cost at larger k?
4. **Retention.** `_prune_usage` runs under `admin_lock` from the middleware. Any chance of a long
   stall on a big table, or the pruning never running on an idle server (acceptable by design)?

## 3. Part B: token scopes (new feature)

**Requirement (from the Governor):** two token kinds. A **read-only** token can only request data
from the SMOS database. A **read/write** token (a parser) can also send data for ingestion. This
separates the two privileges so ordinary users cannot modify or corrupt the database.

**Design as built:**

- The admin picks the scope per invitation (form default: read-only). The claimed token inherits it.
  `scope` is stored in `tokens` and `invitations` (admin DB migration 2; existing rows become `rw`,
  deliberately, so an upgrade cannot lock out a parser).
- `smos/admin/tokens.py`: `SCOPES = ("read", "rw")`, `check_scope`, `authenticate_with_scope`.
- `smos/api/deps.py`: `require_token` stores `request.state.token_scope`; `require_write` returns
  403 unless the scope is exactly `"rw"` (a missing scope is treated as read-only).
- `smos/api/routes.py`: two routers, `read` and `write` (`write` has `dependencies=[Depends(require_write)]`);
  `smos/api/app.py` includes both under the token-protected parent. Write operations' OpenAPI
  descriptions end with "Requires a read/write token."
- Classification: read = `POST /query`, `/recall`, `/graph/query`, `/requirement-context`,
  `GET /nodes/{id}/text`, `GET /verbatim/{key}`. Write = `POST /memories`, `DELETE /memories/{id}`,
  `POST /memories/derived`, `POST /links`, `PUT /nodes/{id}/summary`, `POST /documents`, `POST /verbatim`.
- Known and accepted: searches bump an internal recall counter (`VectorStore.bump_recall`), a usage
  statistic behind the hot/warm/cold tiers. The Governor was told and approved this.

Please look for:

1. **Any way for a read-only token to change database state.** Check every read route and the code
   it reaches (`semantic_query`, `recall` with each strategy, `graph_query`, `requirement_context`,
   `reconstruct`, `retrieve_verbatim`). Anything beyond the recall counter that writes (including
   background effects like lifecycle/tier recomputation or deduplication)?
2. **Bypass of `require_write`.** It depends on `request.state.token_scope` set by an earlier
   dependency. Can ordering, a router mix-up, a lazily included router (FastAPI 0.139), HEAD or
   OPTIONS, a path with a trailing slash or odd encoding, or the OpenAPI/docs dependency
   (`require_token_or_admin_session`) skip the check or leave the scope unset on a write route?
3. **401 vs 403 and information leaks.** An invalid token must be 401 on every route; a valid
   read-only token on a write route must be 403 without performing any work (the body is validated
   by FastAPI, check that no side effect happens before the dependency raises).
4. **Admin side.** Is `scope` validated server-side (unknown values rejected with 400, missing means
   read)? Can the scope of an existing invitation or token be changed or escalated by a user or by a
   forged form? Can a user claim a different scope than the admin chose, including through a race
   between `claimable_scope` (display) and `claim`? Does anything let a read-only user obtain an
   `rw` token?
5. **Migration 2** (`smos/admin/db.py::_add_scope`): `ALTER TABLE ... ADD COLUMN ... DEFAULT 'rw'`
   under the existing runner. Upgrading a populated version-1 admin DB is tested
   (`tests/test_admin_scopes.py::test_migration_2_keeps_existing_tokens_and_invitations_rw`). Is the
   default itself the right call, and are there other places that create tokens or invitations
   with an implicit scope (`create_token` defaults to `rw`, `create_invitation` defaults to `read`)?
6. **The pinned list.** `tests/test_api_scopes.py` pins the exact set of write operations and fails
   when the OpenAPI operations differ from `WRITE | READ | OPEN`. Is that a sound guard, or can a new
   write route slip in classified as read without the test noticing?

## 4. Decisions already made (flag only if harmful)

- `GET /api/v1/health` is open. Admin-session users can read OpenAPI/docs but have no API scope.
- Existing tokens and invitations migrate to `rw`; new invitations default to `read`.
- Recall counters are allowed to change for read tokens.
- Everything listed as a decision in the first review package still stands (in-process locks, one
  shared admin SQLite connection, CDN-hosted Swagger assets, no Docker yet).

## 5. Evidence already collected

- 423 tests pass. New tests: `test_admin_scopes.py` (10), `test_api_scopes.py`, scope cases in
  `test_admin_pages.py`, plus the five fix tests above (each was seen failing before its fix;
  the ingest race reproduced as mixed versions in round 2).
- Live runs: access log shows `/claim/<redacted>` (0 occurrences of the secret); a read-only token
  gets 403 on `POST /documents` and `DELETE /memories/{id}`, 200 on `POST /query` and the OpenAPI JSON;
  the rw token ingests (200); the admin list shows each token's access level; the 403s are in the
  per-token usage view. See `docs/standalone-core-verification.md` §5 and §6.

## 6. How to report

For each finding: file and line, one-sentence defect, a concrete failing request or sequence, and
severity (Critical = auth/scope bypass, token or secret exposure, data loss; Important = wrong
behavior a user will hit; Minor = polish). State which items in sections 2 and 3 you checked and
found sound.

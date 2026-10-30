# Token lifecycle and Settings page: verification record (release 0.4.0, unreleased)

Design `docs/superpowers/specs/2026-10-30-token-lifecycle-and-settings-design.md`, plan
`docs/superpowers/plans/2026-10-30-token-lifecycle-and-settings.md`, runbook `docs/runbooks/backup-and-pentest.md`.
Built test-first, inline, one commit per task (`0900a0e..HEAD`).

## 1. Automated tests

Full suite `python -m pytest tests -q`: **1139 passed** (1078 before this work), about 4 minutes.
New files: `test_token_expiry.py`, `test_admin_rotation.py`, `test_admin_token_pages.py`, `test_storeops.py`,
`test_pentest.py`, `test_backup.py`, `test_backup_load.py`, `test_admin_settings.py`; changed expectations in
`test_api_auth.py` (health now has `pentest`) and one test added to `test_update_apply.py`.

## 2. Real run against a live server

A real `python -m smos.api.main` process on a temporary data directory, driven over HTTP by a script
(admin login, invitation, claim, API calls with the real embedding model). **29 of 29 checks passed:**

- new token expires in about 30 days; no Extend button at 30 days left, button at 2 days left; extend adds exactly 30 days
- rotate: link shown, old token answers 401 at once, claim page says it replaces the old token, new token works, link is single use
- backup downloads a zip; ingest after the backup; load restores the earlier state (first item present, second absent);
  no `store-safety/` or `store-swap.json` left; a garbage file is refused (400) and the data is intact
- pentest: enable shows the banner, health reports `"pentest": true`, the store is empty, a pentest token can be created and used,
  21 items written during the test, **live `metadata.db` and `faiss.index` byte-identical** throughout, backup refused (409);
  disable removes `pentest-store/` and `pentest.json`, health back to false, live data back, pentest data gone,
  the pentest token still exists (removal by hand, as designed)

The server was stopped afterwards and its temporary data deleted.

## 3. Defects found while building (all fixed with tests)

- A store replaced during a swap left its `atexit` hook alive: at shutdown it would have rewritten `faiss.index` over a just-loaded
  backup. `VectorStore.close()` now unregisters the hook, marks the store closed and is idempotent
  (`test_closed_store_never_rewrites_its_index_at_exit`, `test_closing_a_store_twice_is_harmless`).
- `app.state.close()` closed the store captured at startup; after a swap that is the wrong one. It now closes the current store.
- Listing tokens joined invitations by token id; a rotation adds a second invitation per token and would have duplicated rows.
  The list now joins the latest claimed invitation (`test_claim_restores_same_token_with_new_secret`).

## 4. Known limits (not defects)

- A zip's declared sizes are not capped beyond the upload limit (`SMOS_MAX_BACKUP_MB`, default 2048); a deliberately
  crafted high-ratio zip from an admin could fill the disk. Only the admin can upload.
- Starlette spools a multipart upload to a temporary file before the route sees it; the size check beyond the `Content-Length`
  pre-check happens while copying.
- Loading a backup is not atomic with respect to a power cut between the file moves; the swap marker plus safety copy make the
  next start restore the previous store (tested by simulation, not by cutting power).
- The Debian host has not run 0.4.0 yet.

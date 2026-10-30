# Review package 9: token lifecycle and the Settings page (release 0.4.0)

Ninth review round. Earlier: package 8 (resolved in `docs/benchmark-verification.md` section 8). This package covers
`git diff 0900a0e..HEAD` (0900a0e is the commit with the design and plan; design
`docs/superpowers/specs/2026-10-30-token-lifecycle-and-settings-design.md`, plan
`docs/superpowers/plans/2026-10-30-token-lifecycle-and-settings.md`, record `docs/token-settings-verification.md`).
**This one touches authentication and replaces the live data files at runtime**: a wrong token check lets a dead token in or locks a
live one out, and a wrong file move can lose or corrupt the store. Report only; do not edit.

## 1. Scope

- **Token lifecycle (admin DB migration 6, `token_lifecycle`).** `smos/admin/db.py` (new columns `tokens.expires_at`, `tokens.rotating`,
  `invitations.rotate_token_id`; existing tokens get 30 days from the upgrade), `smos/admin/tokens.py` (`TOKEN_TTL_DAYS`,
  `EXTEND_WINDOW_DAYS`, `new_expiry`, `extend_token`, `authenticate_with_scope` rejecting revoked/rotating/expired identically and
  without touching `last_used_at`, `list_tokens` with `state`/`can_extend`/`days_left`), `smos/admin/invitations.py`
  (`create_rotation_invitation`, `is_rotation_invitation`, `claim` replacing the secret of an existing token for a rotation invitation),
  routes in `smos/admin/pages.py` (`/admin/tokens/{id}/extend`, `/rotate`, the claim page), templates `access.html`, `claim.html`,
  `rotation_created.html`.
- **Store swap mechanism.** `smos/admin/storeops.py` (`paused_api`, `replace_store`, `open_store`, `StoreOpRefused`); gate in
  `smos/api/app.py` (`store_op_active` answers 503 on `/api/v1` except health and on `/admin/dashboard`); refusal hooks in
  `smos/bench/service.py::_check_can_start`, `smos/update/apply.py::Updater.begin`, `smos/admin/metrics.py::Sampler.sample_once`;
  `smos/memory/vector_store.py`: `close()` now idempotent and unregisters its `atexit` hook, `health_check`, `write_snapshot`.
- **Pentest mode.** `smos/admin/pentest.py` (`enable`/`disable`, marker `pentest.json`, folder `pentest-store/`, `store_path` used at
  startup), `/api/v1/health` reports `pentest`, banner in `base.html`, `_page` passes the flag.
- **Backup and load.** `smos/admin/backup.py` (`create_backup`, `validate_backup`, `load_backup`, `recover_interrupted_swap`, marker
  `store-swap.json`, folder `store-safety/`), `recover_interrupted_swap` called in `create_app` before the store opens.
- **Settings page.** Routes in `smos/admin/pages.py` (`/admin/settings` and four POSTs), `settings.html`, nav link.
- Docs touched: `docs/API.md`, `docs/INSTALL.md`, `docs/runbooks/backup-and-pentest.md` (new), two guide files, the design (manifest
  counts), `smos/__init__.py` (0.4.0).
- Tests: `test_token_expiry.py`, `test_admin_rotation.py`, `test_admin_token_pages.py`, `test_storeops.py`, `test_pentest.py`,
  `test_backup.py`, `test_backup_load.py`, `test_admin_settings.py` (+ `test_api_auth.py`, `test_update_apply.py`). Suite: 1139 passed.
- Not changed (on purpose, so the dashboard update works without a rebuilt image): `pyproject.toml`, `Dockerfile`, `compose.yaml`,
  `models/`, `smos/launcher.py`.

## 2. Where to look hardest

1. **Authentication** (`tokens.authenticate_with_scope`, `invitations.claim`). Comparison is on ISO-8601 strings produced by
   `datetime.now(timezone.utc).isoformat()`; check boundary behaviour (a time with no fractional seconds sorts differently from one with
   them) and that `claim` for a rotation cannot revive a revoked token or leave a half-applied state (it rolls back the whole claim).
   `create_rotation_invitation` sets `rotating = 1` and commits before the link is shown: is there any path where the old secret still works?
2. **Store swap** (`storeops.paused_api`, `pentest.enable/disable`, `backup._swap_in`). Requests counted by `api_inflight` are drained, but
   a request admitted just before the flag was set and a background thread (sampler, usage pruning) are not the same thing: look for any
   reader of `app.state.store` that caches the object, and for a window where a request touches a closed store.
3. **Load backup file moves** (`backup._swap_in`, `_restore_safety`, `recover_interrupted_swap`). Order of marker, safety folder, moves and
   deletes; what a crash at each point leaves; Windows file handles (the old store must be fully closed before moving); SQLite `-wal` and
   `-shm` side files; the restore path when the restore itself fails.
4. **Zip handling** (`backup.validate_backup`). Members are extracted by exact name; check traversal, duplicate member names (the
   `namelist` is sorted and compared, a duplicate entry changes its length), zip bombs (declared sizes are not capped beyond the upload
   limit; a known limit), and that migration of an older-schema backup runs on the extracted copy only.
5. **Pentest mode correctness claim**: "the live store is never opened for writing while the mode is on". `enable` opens the throwaway
   store before closing the live one; `disable` opens the live one before closing the throwaway. Confirm the live files cannot be written
   in between, and that a restart in either state opens the right store (`pentest.store_path`).
6. **Concurrency of the admin operations** with each other, an update and a benchmark (`transition_lock`, `store_op_active`): any
   double-click, back-to-back or interleaved case that gets two operations running.
7. **Upload route** (`settings_load`): the order of CSRF check, size check and file write; temp file cleanup on every path.

## 3. Behaviour agreed with the Governor (not findings)

Tokens expire after 30 days; Extend only in the last 3 days (adds 30 days to the old expiry); an expired token needs Rotate; rotation gives
a fresh 30 days, keeps the token's id, name, scope and history, and the old secret dies at once; backups never contain `admin.db`;
tokens made during a pentest are removed by hand; disabling pentest mode deletes the throwaway store; health reports pentest mode; load
keeps a safety copy until the loaded store passes an automatic check.

## 4. Known limits (see `docs/token-settings-verification.md` section 4)

Uncapped declared zip size, multipart spooling before the route runs, no power-cut test, Debian not yet run.

## 5. Questions

Report any case where a token that should be dead authenticates, where data can be lost or silently replaced, or where SMOS can come
up reading the wrong store. Grade by effect on a person using the service, not by whether the design names the input.

# Token lifecycle and Settings page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the Governor chose inline execution; no reviewer subagents, review inline). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tokens expire (30 days), can be extended and rotated; a new Settings page offers store backup, load backup and pentest mode.

**Architecture:** Token lifecycle is admin-DB only (migration 6, `tokens.py`, `invitations.py`, two routes). Backup, load and pentest mode are store-level operations that share one mechanism: pause `/api/v1`, drain, swap `app.state.store`, resume (`smos/admin/storeops.py`). Pentest mode keeps the live store files untouched and runs an empty store in `<data>/pentest-store/`.

**Tech Stack:** Python, FastAPI, SQLite (backup API), FAISS, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-token-lifecycle-and-settings-design.md`

## Global Constraints

- Develop on `main`; test command `rtk proxy python -m pytest tests -q` (about 3.5 min); never `python3`.
- New table columns need a **new numbered migration** (admin migration 6); never edit an old one.
- Every `/api/v1` route except `/health` stays behind `require_token`; every new `/admin` POST needs the admin session and `_check_csrf`.
- Files that force a rebuilt image are **not touched**: `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/`, `smos/launcher.py`.
- Tokens: `TOKEN_TTL_DAYS = 30`, `EXTEND_WINDOW_DAYS = 3`, rotation invitation TTL 72 h (the existing `SMOS_INVITE_TTL_HOURS` default). Expired, rotating, revoked and unknown tokens all give the same 401.
- Backups never contain `admin.db`. The health endpoint reports `"pentest": true|false` and nothing else new.
- No company/benchmark data in the repo. Version becomes `0.4.0` in `smos/__init__.py` only (see `docs/RELEASING.md`); do not tag, push or publish.
- Commit after each task with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A rejected (expired/rotating/revoked) token must not get `last_used_at` updated, and a token expiring at exactly `now` is expired. (Task 1)
2. A rotation link replayed, superseded by a newer Rotate, or claimed after the token was revoked must fail and never revive the token. (Task 2)
3. A backup zip with path-traversal member names, extra members, a wrong hash, or a store schema newer than the code is refused with the live store byte-identical. (Task 6, 7)
4. A request still in flight when a swap starts: the drain times out, the flag is cleared, nothing was touched. Two operations at once (double click, backup during load): the second is refused. (Task 4, 7)
5. SMOS killed mid-load or while in pentest mode: the next start restores the safety copy / stays in pentest mode and never opens a half-swapped live store. (Task 5, 7)

---

### Task 1: Token expiry and extension

**Files:**
- Modify: `smos/admin/db.py` (migration 6), `smos/admin/tokens.py`
- Test: `tests/test_admin_tokens.py`, `tests/test_admin_migrations.py` (or the existing migration test file; find it with grep `ADMIN_MIGRATIONS`)

**Interfaces:**
- Produces in `tokens.py`: `TOKEN_TTL_DAYS`, `EXTEND_WINDOW_DAYS`; `extend_token(conn, token_id: int) -> bool`; `list_tokens` rows gain `expires_at: str`, `rotating: bool`, `days_left: int | None`, `state: str` (`"active" | "expired" | "rotating" | "revoked"`, checked in that order of precedence: revoked, rotating, expired, active), `can_extend: bool`.
- Migration 6 `token_lifecycle` adds `tokens.expires_at TEXT NOT NULL DEFAULT ''` (existing rows set to migration time + 30 days), `tokens.rotating INTEGER NOT NULL DEFAULT 0`, `invitations.rotate_token_id INTEGER`.

- [ ] **Step 1: Write failing tests**
  - `test_new_token_expires_in_30_days`: `expires_at` is `created + 30 d` (±1 min).
  - `test_authenticate_rejects_expired_token`: set `expires_at` to a past time by SQL; `authenticate` is None; `last_used_at` stays NULL (Review Focus 1). Same for `expires_at == now` boundary (use a time 1 s in the past).
  - `test_authenticate_rejects_rotating_token`: `UPDATE tokens SET rotating=1` makes it None.
  - `test_extend_only_inside_window`: 10 days left -> `extend_token` False, unchanged; 2 days left -> True and `expires_at` = old + 30 d; expired, revoked, rotating -> False.
  - `test_list_tokens_state_and_can_extend`: one row per state; `can_extend` true only for the 2-day token.
  - `test_migration_6_gives_existing_tokens_30_days`: build a DB at version 5 (run `ADMIN_MIGRATIONS[:5]` via `run_migrations`), insert a token row, run the full list, assert `expires_at` about now + 30 d and `rotating == 0`.
- [ ] **Step 2: Run them; expect FAIL** (`pytest tests/test_admin_tokens.py -q`).
- [ ] **Step 3: Implement.** `_token_lifecycle` in `db.py` appended to `ADMIN_MIGRATIONS` as `(6, "token_lifecycle", ...)`. `insert_token` writes `expires_at`; `authenticate_with_scope` selects `revoked, rotating, expires_at` and returns None before touching `last_used_at` when `revoked or rotating or expires_at <= now_iso()`. Grep tests and code for raw `INSERT INTO tokens` and fix them.
- [ ] **Step 4: Run `pytest tests/test_admin_tokens.py tests/test_admin_invitations.py tests/test_admin_scopes.py -q`; expect PASS.**
- [ ] **Step 5: Commit** `feat: token expiry (30 days), extension window, migration 6`.

### Task 2: Token rotation (core)

**Files:**
- Modify: `smos/admin/invitations.py`
- Test: `tests/test_admin_rotation.py` (new)

**Interfaces:**
- Consumes: Task 1 columns, `tokens.insert_token`, `tokens.hash_secret`.
- Produces: `create_rotation_invitation(conn, token_id: int, admin_username: str, ttl_hours: int = 72) -> tuple[int, str] | None` (None when the token is missing or revoked; returns `(invitation_id, secret)`); `is_rotation_invitation(conn, secret: str) -> bool`; `list_invitations` rows gain `rotation: bool`; `claim()` unchanged signature, but a rotation invitation replaces the existing token's secret and returns `(token_id, plaintext)`.

- [ ] **Step 1: Write failing tests**
  - `test_rotate_kills_old_secret_at_once`: after `create_rotation_invitation`, old plaintext authenticates to None.
  - `test_claim_restores_same_token_with_new_secret`: claim returns the same `token_id`, a different plaintext; new plaintext authenticates; old does not; `expires_at` about now + 30 d; `rotating == 0`; name, scope and `usage_log` rows for that token id unchanged; invitation has `token_id` and claim ip/agent recorded.
  - `test_expired_token_can_be_rotated_and_recovered`.
  - `test_new_rotation_supersedes_old_link` (Review Focus 2): the first link's claim returns None, the second works.
  - `test_replayed_rotation_link_fails`; `test_rotation_link_after_revoke_fails_and_does_not_revive`: revoke the token, then claim -> None and the token stays unusable.
  - `test_rotation_for_revoked_or_unknown_token_returns_none`.
  - `test_unclaimed_rotation_leaves_token_unusable_until_expiry_then_rotate_again`.
  - `test_rotation_invitation_listed_with_rotation_flag`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** `create_rotation_invitation` is one transaction: `UPDATE tokens SET rotating=1 WHERE id=? AND revoked=0` (rowcount 0 -> rollback, None); revoke earlier unclaimed invitations with the same `rotate_token_id`; insert the invitation (label = token name, scope = token scope, `rotate_token_id`). In `claim`, when the claimed row has `rotate_token_id`, replace `token_hash` with a fresh `smos_` secret, set `expires_at`, `rotating=0` guarded by `revoked = 0`; if that update touches no row, roll back the whole claim and return None.
- [ ] **Step 4: Run `pytest tests/test_admin_rotation.py tests/test_admin_invitations.py -q`; PASS.**
- [ ] **Step 5: Commit** `feat: token rotation via claim link`.

### Task 3: Access page: Expires column, Extend, Rotate

**Files:**
- Modify: `smos/admin/pages.py`, `smos/admin/templates/access.html`, `smos/admin/templates/claim.html`
- Create: `smos/admin/templates/rotation_created.html`
- Test: `tests/test_admin_pages.py`

**Interfaces:**
- Consumes: Task 1 `extend_token`, `list_tokens`; Task 2 `create_rotation_invitation`, `is_rotation_invitation`.
- Produces routes: `POST /admin/tokens/{token_id}/extend` (303 to `/admin/tokens`; 409 page with message when `extend_token` is False), `POST /admin/tokens/{token_id}/rotate` (renders `rotation_created.html` with the link and TTL once; 404 when None).

- [ ] **Step 1: Write failing tests** (reuse `_login`, `_csrf`): Tokens table shows an Expires value and `expired: rotate needed` / `rotation pending` states; the Extend button appears only for a token with 2 days left; extend POST adds 30 days; extend outside the window gives 409 and no change; rotate POST shows a `/claim/...` link, the old bearer token then gets 401 on `/api/v1`, the claim page for a rotation link says it replaces the old token, and completing the claim yields a working token; both POSTs without a CSRF field give 403 and without login 303.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** routes inside `register_admin` next to the revoke routes, each with `_check_csrf` and `admin_lock`; template columns per the agreed mockup (Extend shown when `t.can_extend`, Rotate for every non-revoked token, Revoke as today); `claim_page` passes `rotation=is_rotation_invitation(...)` to `claim.html`.
- [ ] **Step 4: Run `pytest tests/test_admin_pages.py tests/test_api_auth.py -q`; PASS.**
- [ ] **Step 5: Commit** `feat: access page extend and rotate`.

### Task 4: Store operation mechanism (pause, drain, swap)

**Files:**
- Create: `smos/admin/storeops.py`
- Modify: `smos/api/app.py` (use `open_store`, new state, gate), `smos/bench/service.py` (`_check_can_start`), `smos/update/apply.py` (`begin`), `smos/admin/metrics.py` (skip a tick while a store op is active)
- Test: `tests/test_storeops.py` (new)

**Interfaces:**
- Produces in `storeops.py`: `class StoreOpRefused(RuntimeError)`; `open_store(path: Path) -> VectorStore` (creates the store and a `LifecycleManager`, sets `store._lifecycle_callback`); `paused_api(app, drain_timeout: float = 30.0)` a context manager; `replace_store(app, path: Path) -> None` (closes `app.state.store`, sets `app.state.store = open_store(path)`, must only be called inside `paused_api`).
- `app.state` gains `store_op_active: bool = False`, `pentest: bool = False`, `live_dir: Path` (the data dir).
- `paused_api`: under `transition_lock` refuse (`StoreOpRefused` with a plain message) if `store_op_active`, `benchmark_active`, `maintenance`, `closing`, or the updater state is not idle; set `store_op_active = True`; then wait for `api_inflight == 0` up to the timeout (poll like `BenchmarkService._drain`), on timeout clear the flag and raise; always clear the flag on exit.
- The gate in `create_app` answers 503 + `Retry-After` for `/api/v1` (except health) **and** `/admin/dashboard` while `store_op_active`. Benchmark start and `Updater.begin` refuse while it is set.

- [ ] **Step 1: Write failing tests:** flag set inside and cleared after (also after an exception); a second `paused_api` while one is open raises `StoreOpRefused` (Review Focus 4); refused while `benchmark_active`; drain timeout (set `api_inflight = 1`, timeout 0.1) raises, clears the flag and leaves `app.state.store` the same object; `/api/v1/query`-style call returns 503 during the pause and `/api/v1/health` 200; `replace_store` gives a store on the new path, old one closed, lifecycle callback attached; benchmark start and updater `begin` refuse while the flag is set.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Move the store-and-lifecycle construction in `create_app` to `open_store`. Verify every reader of `app.state.store` (`routes.py`, `dashboard_view.py`, `metrics.py`) reads the attribute per use and never caches the object.
- [ ] **Step 4: Run `pytest tests/test_storeops.py tests/test_bench_service.py tests/test_update_apply.py -q` (use the real file names from grep); PASS.**
- [ ] **Step 5: Commit** `feat: store operation pause/drain/swap mechanism`.

### Task 5: Pentest mode

**Files:**
- Create: `smos/admin/pentest.py`
- Modify: `smos/api/app.py` (startup path, health), `smos/admin/pages.py` (`_page` passes `pentest`), `smos/admin/templates/base.html` (banner)
- Test: `tests/test_pentest.py` (new)

**Interfaces:**
- Consumes: Task 4 `paused_api`, `replace_store`, `open_store`.
- Produces: `PENTEST_DIR = "pentest-store"`, `MARKER = "pentest.json"`; `is_active(data_dir: Path) -> bool` (marker exists); `store_path(data_dir: Path) -> Path` (the pentest dir when active, else `data_dir`; used by `create_app` to open the store and set `app.state.pentest`); `enable(app) -> None`; `disable(app) -> None`; both raise `StoreOpRefused` when already in that state.
- `/api/v1/health` returns `{"status", "version", "pentest": bool}`.

- [ ] **Step 1: Write failing tests:** enable: queries see an empty store, the live `metadata.db` and `faiss.index` are byte-identical (hash before/after) and never written while on; health shows `pentest: true`; the banner text is in every admin page; disable removes `pentest-store/` and the marker, the original documents are queryable again, `pentest: false`; existing and newly created tokens work in both states and `admin.db` is unchanged by the toggles; enable twice and disable while off raise `StoreOpRefused`; a new `create_app` on the same data dir while the marker exists comes up in pentest mode (Review Focus 5); a marker with a missing pentest dir starts with a fresh empty one; a failure while opening the pentest store removes the marker and keeps the live store.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Enable: inside `paused_api` write the marker (write to temp, then `os.replace`), remove any stale pentest dir, `replace_store(app, data_dir/PENTEST_DIR)`, set `app.state.pentest = True`. Disable: inside `paused_api` close the pentest store, remove the marker **first**, open the live store, then `shutil.rmtree` the pentest dir, set the flag False.
- [ ] **Step 4: Run `pytest tests/test_pentest.py tests/test_api_auth.py -q`; PASS.**
- [ ] **Step 5: Commit** `feat: pentest mode with disposable store`.

### Task 6: Backup creation and validation

**Files:**
- Create: `smos/admin/backup.py`
- Modify: `smos/memory/vector_store.py` (add `write_snapshot`)
- Test: `tests/test_backup.py` (new)

**Interfaces:**
- Produces: `VectorStore.write_snapshot(dest_dir: Path) -> None` (under `self._lock`: SQLite backup API to `dest_dir/metadata.db`, `faiss.write_index` to `dest_dir/faiss.index`); in `backup.py`: `class BackupError(ValueError)`; `create_backup(app) -> Path` (zip in the data dir, caller deletes it; refused with `StoreOpRefused` in pentest mode; runs inside `paused_api`); `validate_backup(zip_path: Path, workdir: Path) -> dict` (returns the manifest; leaves verified, already-migrated `metadata.db` and `faiss.index` in `workdir`).
- Zip members: exactly `manifest.json`, `metadata.db`, `faiss.index`. Manifest: `format` (1), `smos_version`, `created_at`, `counts` (`memories`, `vectors`), `sha256` per file.

- [ ] **Step 1: Write failing tests:** round trip (create, validate, manifest counts match the store, files hash-match); extra member, member named `../evil`, missing member, wrong sha256, truncated zip, non-zip bytes, `format` 2, and a store newer than code (patch the copy's version table one step ahead, expect `SchemaTooNewError` turned into `BackupError`) each raise `BackupError` (Review Focus 3); vector count differing from the `memories` count raises `BackupError`; `integrity_check` failure (corrupt the db file) raises; the zip contains no admin-DB content (no token hash from `admin.db`); creation in pentest mode is refused; creation does not leave the API paused.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.** Validation extracts members by exact name (never by archive path), checks hashes, runs `run_migrations(conn, STORE_MIGRATIONS, path)` on the extracted copy (this also migrates older backups and raises `SchemaTooNewError` for newer ones), then `PRAGMA integrity_check`, `faiss.read_index`, and compares `ntotal` with the `memories` row count.
- [ ] **Step 4: Run `pytest tests/test_backup.py tests/test_store_connection_sharing.py -q`; PASS.**
- [ ] **Step 5: Commit** `feat: store backup file and validation`.

### Task 7: Load backup with safety copy and recovery

**Files:**
- Modify: `smos/admin/backup.py`, `smos/memory/vector_store.py` (add `health_check`), `smos/api/app.py` (call `recover_interrupted_swap` before opening the store)
- Test: `tests/test_backup_load.py` (new)

**Interfaces:**
- Consumes: Task 4, Task 5 (`is_active`), Task 6 (`validate_backup`).
- Produces: `load_backup(app, upload_path: Path) -> dict` (returns the manifest; raises `BackupError` / `StoreOpRefused`); `recover_interrupted_swap(data_dir: Path) -> None`; `VectorStore.health_check() -> None` (raises `RuntimeError` when `integrity_check` fails or the memories count differs from the vector count).
- Files involved: live `metadata.db`, `metadata.db-wal`, `metadata.db-shm`, `faiss.index` in the data dir; safety dir `<data>/store-safety/`; marker `<data>/store-swap.json`.
- Order inside `load_backup`: refuse in pentest mode; validate into a workdir (live untouched on failure); `paused_api`; write marker; close store; move live files to the safety dir; copy validated files in; open the store; run `health_check()`, compare counts with the manifest and run one query through the store's normal query path; on success delete the safety dir then the marker; on **any** failure after the move: close the store, restore the safety files, reopen, remove the marker, re-raise as `BackupError` with the reason.

- [ ] **Step 1: Write failing tests:** round trip: ingest A, backup, ingest B, load, only A is queryable, safety dir and marker gone, API resumed; every refusal from Task 6 leaves live files hash-identical and the API unpaused; forced failure after the move (monkeypatch `health_check` to raise) restores the original store and its data; load refused in pentest mode; a second load while one runs is refused; `recover_interrupted_swap` with marker plus safety dir restores the safety files over a half-copied live store (simulate a crash by writing the marker and moving files by hand), and with a marker but no safety dir only removes the marker (Review Focus 5); an older-schema backup loads (migrated).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** per the order above; `create_app` calls `recover_interrupted_swap(data_dir)` before `open_store`.
- [ ] **Step 4: Run `pytest tests/test_backup_load.py tests/test_backup.py tests/test_pentest.py -q`; PASS.**
- [ ] **Step 5: Commit** `feat: load backup with safety copy and crash recovery`.

### Task 8: Settings page

**Files:**
- Create: `smos/admin/templates/settings.html`
- Modify: `smos/admin/pages.py`, `smos/admin/templates/base.html` (nav link "Settings")
- Test: `tests/test_admin_settings.py` (new)

**Interfaces:**
- Consumes: Tasks 5-7 functions.
- Produces routes (all admin session + CSRF, in `register_admin`): `GET /admin/settings`; `POST /admin/settings/backup` (returns the zip as an attachment `smos-backup-<UTC timestamp>.zip`, `Cache-Control: no-store`, temp file deleted by a background task); `POST /admin/settings/load` (multipart field `file`, streamed to a temp file in the data dir, size cap `SMOS_MAX_BACKUP_MB` default 2048 -> 413; result shown on the settings page; the temp file is always deleted); `POST /admin/settings/pentest/enable` and `.../disable` (form field `confirm` must equal `PENTEST`, else 400 and nothing changes). `StoreOpRefused` -> 409 and `BackupError` -> 400, message shown on the page.

- [ ] **Step 1: Write failing tests:** GET shows the three sections and the pentest state/banner; anonymous -> 303; every POST without CSRF -> 403; backup POST returns a valid zip (`validate_backup` passes); load POST with a good file shows success, with garbage shows the error and keeps data; oversized upload (set the env to 1 byte) -> 413; pentest enable without `confirm` -> 400 and `app.state.pentest` False, with `confirm=PENTEST` -> on; disable likewise; backup and load buttons disabled in the template while pentest is on and refused by the routes.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** routes and template following the agreed mockup (use `run_in_threadpool` for the blocking operations as the update routes do).
- [ ] **Step 4: Run `pytest tests/test_admin_settings.py tests/test_admin_pages.py -q`; PASS.**
- [ ] **Step 5: Commit** `feat: settings page (backup, load, pentest mode)`.

### Task 9: Docs, version, full verification, Codex review package

**Files:**
- Modify: `smos/__init__.py` (`0.4.0`), `docs/API.md` (health field), `docs/INSTALL.md` or the admin guide where tokens are described, `docs/HANDOFF.md`, `docs/superpowers/specs/2026-10-30-token-lifecycle-and-settings-design.md` (manifest counts line: `memories` and `vectors`)
- Create: `docs/runbooks/backup-and-pentest.md`, `docs/token-settings-verification.md`, `docs/superpowers/review/2026-10-30-9-review-package.md`

- [ ] **Step 1:** Bump `__version__`; run `pytest tests/test_versioning.py -q`.
- [ ] **Step 2:** Write the runbook (take a backup, load one, what pentest mode does, how to recover by hand from `store-safety/`, `store-swap.json`, `pentest.json`) and update API/admin docs, with no company data.
- [ ] **Step 3:** Drive the real flow on a running local server (start `smos-server` with a temp `SMOS_DATA_DIR`, bring it down afterwards): create invitation, claim, extend (shorten expiry by SQL to test), rotate and claim; backup download; ingest; load; pentest on/off with the live store hash checked. Record results in `docs/token-settings-verification.md`.
- [ ] **Step 4:** Run the full suite `rtk proxy python -m pytest tests -q`; expect all pass (1078 before this work).
- [ ] **Step 5:** Write the review package in the same format as `docs/superpowers/review/2026-10-29-8-review-package.md`: scope with file lists, the highest-risk points (token auth path, store swap and file moves, zip handling, crash recovery), the diff range from the commit before Task 1 to HEAD, what was not changed on purpose, and the instruction "report only; do not edit".
- [ ] **Step 6:** Update `docs/HANDOFF.md`; commit `docs: token lifecycle and settings verification, review package 9`. Do not tag, push or publish; the release waits for the Codex review and the Governor.

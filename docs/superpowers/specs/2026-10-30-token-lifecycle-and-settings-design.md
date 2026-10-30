# Token lifecycle and the Settings page — design

Date: 2026-10-30. Status: draft for the Governor's approval. Target: the next minor release
(proposed **0.4.0**, since it adds features and a migration; the Governor may prefer 0.3.5).

## Intent

SMOS is going to a company trial and will be pentested. Three needs follow:

1. Tokens must not live forever: they expire, can be extended, and can be replaced
   (rotated) without the admin ever seeing the secret.
2. The admin must be able to take a backup of the data and load it back safely.
3. A pentester must be able to hammer the API without touching the live data: a
   disposable store takes the live store's place for the duration of the test.

## Goals

- Tokens expire 30 days after they are created or claimed. Expired tokens stop working.
- Admin can extend a token by 30 days when it expires within 3 days.
- Admin can rotate a token. The old secret stops working at once. A new single-use claim
  link is shown once to the admin; the token's user opens it and gets a new secret.
- New admin page `/admin/settings` with: backup download, load backup, pentest mode.
- A "PENTEST MODE ACTIVE" banner on every admin page while pentest mode is on.

## Non-goals / anti-goals

- No automatic cleanup of tokens or invitations made during a pentest (the admin removes
  them by hand).
- Backups never contain `admin.db` (tokens, invitations, usage log, admin accounts). A lost
  `admin.db` is recreated from scratch, not restored.
- No scheduled backups, no backup retention, no remote backup storage.
- No files that force a rebuilt image change (`pyproject.toml`, `Dockerfile`, `compose.yaml`,
  `models/`, `smos/launcher.py`), so the dashboard update keeps working.
- The admin must never be able to see a token secret (unchanged).

## Part 1 — Token expiry, extension, rotation

### Data (admin DB migration 6, `token_lifecycle`; never edit older migrations)

`tokens` gets two columns:

- `expires_at TEXT NOT NULL` (ISO UTC, same format as `now_iso()`). The migration sets every
  existing token to the migration time + 30 days, so no token locks out on upgrade.
- `rotating INTEGER NOT NULL DEFAULT 0`.

`invitations` gets `rotate_token_id INTEGER` (NULL for ordinary invitations).

### Behaviour

- **Create:** `insert_token` sets `expires_at = now + 30 days`. The 30 days is one constant
  (`TOKEN_TTL_DAYS`), not a setting.
- **Authenticate:** a token is valid only if `revoked = 0`, `rotating = 0` and
  `expires_at > now`. An expired, rotating or revoked token gets the same answer as an
  unknown one (401); nothing tells the caller which case it was.
- **Extend:** allowed only when the token is active (not revoked, not rotating, not
  expired) and `expires_at - now <= 3 days`. Sets `expires_at = expires_at + 30 days`
  (added to the old expiry, so extending early gives no more or less than 30 days;
  *builder's call*). Otherwise refused with a message. The button shows only in that window.
- **Rotate:** allowed on any token that is not revoked (including expired ones: an expired
  token can only be recovered by rotating). In one transaction: set `rotating = 1`, revoke
  any earlier unclaimed rotation invitation for that token, create a new invitation with
  `rotate_token_id = token_id`, same scope, label = the token's name, TTL 72 hours like
  other invitations (*builder's call*). The link is shown once, on a page like the
  existing invitation-created page. The old secret is dead from the moment of this commit.
- **Claim of a rotation invitation:** the existing claim page and "Create token" button, but
  instead of inserting a new token row it replaces `token_hash` on the existing row, sets
  `expires_at = now + 30 days`, `rotating = 0`, and records the claim (time, IP, user agent)
  on the invitation as today. Token id, name, scope and usage history are kept. The claim
  page tells the user that it replaces their old token.
- **Rotation link not claimed in time:** the token stays unusable (`rotating` stays 1). The
  admin presses Rotate again for a new link, or Revokes it (*builder's call*).
- **Access page:** Tokens table gets an Expires column ("in N days" / "expired") and
  buttons Extend (in the window), Rotate, Revoke. Status shows `rotation pending` or
  `expired: rotate needed`. Mockup was agreed in the brainstorm.
- All new routes need the admin session and CSRF, like the existing token routes. Token
  routes under `/api/v1` are unchanged; `test_every_route_in_schema_is_protected` stays green.

## Part 2 — Settings page

New nav entry and `smos/admin/templates/settings.html`; new module `smos/admin/settings_ops.py`
(or similar) for the operations below, routes in `pages.py`. One file operation at a time:
backup, load and pentest toggle all take the existing `transition_lock` (so they never run
together with each other, an update or a benchmark; refused with a clear message if it is
held).

### Pausing the API for a store operation

Same mechanism as the benchmark start: set a flag (generalise `benchmark_active` or add
`store_swap_active`) so `/api/v1` answers 503 with `Retry-After` (except health), then wait
for `api_inflight` to reach zero (bounded wait, as the benchmark does) before touching
files. The flag is always cleared in a `finally`.

### Backup

- Produces one downloadable file: a zip with `manifest.json`, `metadata.db`, `faiss.index`.
- `metadata.db` is written with the SQLite backup API (consistent copy, not a raw file
  copy); the FAISS index is saved while the API is paused so the pair matches.
- Manifest: SMOS version, store schema version, created-at, row counts (documents,
  segments), SHA-256 of each file.
- Not available while pentest mode is on (the live store is not the open store).

### Load backup

Steps, in order; any failure before step 4 leaves the live store untouched:

1. Upload to a temp file in the data directory (size limit from a setting with a generous
   default; *builder's call*).
2. Validate offline: zip is sane, manifest present, hashes match, store schema version is
   not newer than this code (`SchemaTooNewError` rule), SQLite `integrity_check` passes on
   the extracted `metadata.db`, the FAISS index loads and its vector count matches the
   store's segment mapping.
3. Pause the API and wait for it to drain.
4. Move the live store to a safety directory (`<data>/store-safety/`), put the backup files
   in place, reopen the store (older schema versions are migrated by the normal runner).
5. Automatic check on the loaded store: integrity check, counts match the manifest, one
   test query runs. Pass: delete the safety directory, resume the API. Fail: close the
   store, restore the safety directory, reopen, resume, and show the reason.
6. A marker file (`<data>/store-swap.json`) is written before step 4 and removed at the
   end. If SMOS dies in between, startup sees the marker and restores the safety directory
   before opening the store.

The running app swaps `app.state.store` (and re-attaches the lifecycle callback) under the
pause; nothing may keep a reference to the old store (the builder verifies every user of
`app.state.store`, including the sampler and dashboard).

### Pentest mode

- **Enable:** pause the API, close the live store, create an empty store in
  `<data>/pentest-store/`, write `<data>/pentest.json`, open the new store as
  `app.state.store`, resume. The live store files are **never moved or opened** while the
  mode is on.
- **Disable:** pause, close the pentest store, delete `pentest-store/`, remove
  `pentest.json`, reopen the live store, resume.
- **Persistence:** startup reads `pentest.json`; if present, SMOS comes up in pentest mode.
  A crash or an update restart never silently reopens the live store.
- `admin.db` is untouched by both directions: existing tokens keep working (against the
  empty store), pentest tokens come from the normal invitation flow, and the admin clears
  them by hand afterwards.
- Banner on every admin page while on (from `app.state`, rendered in `base.html`).
  The unauthenticated `/api/v1/health` also reports the mode (`"pentest": true|false`;
  the Governor's decision), so a pentester or monitor can see it. It exposes nothing else.
- Backup and Load backup are refused while the mode is on.
- Enabling is behind a confirmation step (the admin types the word `PENTEST`) so it cannot
  be triggered by accident; disabling is also confirmed (it deletes the throwaway data).

## Tests (test-first)

- Migration 6: existing tokens get an expiry about 30 days out; fresh databases too.
- Authenticate rejects expired, rotating and revoked tokens identically.
- Extend: refused outside the 3-day window, refused when expired or rotating, adds 30 days.
- Rotate: old secret dead immediately; claim restores access with a new secret and 30 days;
  same token id and usage history; old rotation link revoked by a newer one; a replayed
  rotation link fails.
- Backup/load round trip; a corrupted zip, a hash mismatch, a too-new schema and a
  failing post-load check each leave the live store intact; crash-marker recovery.
- Pentest: enable shows an empty store and leaves the live files byte-identical; disable
  restores and deletes the throwaway; restart while on stays on; backup/load refused.
- Auth guard tests and the admin CSRF tests cover the new routes.

## Left to the builder

Page wording, exact route names, where the helper code lives, the load size limit, how the
bounded drain wait is shared with the benchmark code, and the changelog / `docs/RELEASING.md`
steps. Update `docs/API.md`/admin docs, `docs/HANDOFF.md`, and add a runbook entry for
backup/restore and pentest mode in `docs/runbooks/`.

## Decisions confirmed by the Governor

- Disabling pentest mode **deletes** the throwaway database and restores the live one.
- The health endpoint exposes pentest mode.

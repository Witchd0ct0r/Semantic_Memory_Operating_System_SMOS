# SMOS Self-Update — Design (Project 4)

Status: draft for Governor approval. Date: 2026-10-28.

## Intent

An installed SMOS (Docker, on a machine the Governor can reach but does not want to SSH into for every
change) can update itself to the newest code on the public GitHub repo's `main`, from a button on the
dashboard, without rebuilding the image and without losing data. If the new code does not come up
healthy, the installation returns to exactly the state it had before the click.

## Goals

- Dashboard shows the running version (commit) and whether the remote branch is ahead, and by how many commits.
- One admin click applies an update: download, verify, back up, swap code, restart, health-check.
- Automatic rollback (code **and** data) if the new code fails to start or fails its health check.
- Says plainly when an update needs a real rebuild (dependencies, Dockerfile, compose, base image) and then
  refuses to apply it, instead of half-applying.
- No Docker socket, no privileged container, no host-side agent required.

## Non-goals

- No automatic or scheduled updates. Every update is a click.
- No updating from arbitrary URLs, forks, other branches, or a chosen older commit (no downgrade path in v1).
- No update of dependencies, the base image, the bundled embedding model, or `compose.yaml` — those need a rebuild.
- No signature verification of commits (see Security: the trust root is the repo owner's GitHub account).
- No multi-instance coordination; one container per data volume.

## Anti-goals (a failure even if it "works")

- An update that leaves data and code mismatched (new schema, old code) after a failed start.
- Any request-supplied value (URL, ref, path) influencing what is downloaded or where it is written.
- Update traffic needing anything the existing deploy does not already have (CA bundle, optional proxy).

## Constraints

- Public repo: `https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS` (no credential handling).
  The repo name is configuration, not hard-coded logic.
- The target network re-signs HTTPS: the update client must trust the extra CA bundle already copied to
  `/opt/smos/certs/ca-bundle.crt` when present, and honour `HTTPS_PROXY` / `NO_PROXY`.
- Unauthenticated GitHub API: 60 requests/hour per IP. Cache and rate-limit checks.
- Migrations are forward-only (`smos/storage/migrations.py`); an older codebase refuses newer data
  (`SchemaTooNewError`). Rollback of code therefore requires restoring data taken before the update.
- Container runs as non-root user `smos`; only `/data` is writable. Code overlay lives in `/data`.
- No company/host names in the repo (standing rule).

## Design

### 1. Launcher (the only new always-on piece)

`smos-server` becomes a small supervisor (`smos/launcher.py`, kept tiny and stable because it is part of the image
and cannot be updated by an overlay). It runs the real app as a child process (`python -m smos.api.main`) with the
overlay first on `PYTHONPATH` when a valid overlay exists, forwards SIGTERM, and relaunches the child when it
exits with code **75** ("restart requested"). Any other child exit ends the launcher (Docker's
`restart: unless-stopped` then applies as today).

### 2. On-disk layout (`/data/updates/`)

```
overlay/<sha>/smos/...      unpacked code of an applied update (only the smos/ package)
current -> overlay/<sha>    pointer file `current.json` {sha, base_build_id, applied_at}; absent = baked code
pending.json                {target_sha, staged_dir, requested_by, requested_at}  written by the app
snapshot/                   copy of the data files taken just before the swap (one kept)
last-result.json            {ok, from_sha, to_sha, finished_at, error}  shown on the dashboard
```

Use a pointer file, not a symlink (portable to Windows dev, simpler to make atomic via write-temp-then-rename).

### 3. Version identity and "needs rebuild"

- Build writes `/opt/smos/build-info.json`: `{commit, build_id, manifest}`. `commit` comes from a build arg
  `SMOS_COMMIT` (install guide: `SMOS_COMMIT=$(git rev-parse HEAD) docker compose build`; the compose file passes
  it through, defaulting to `unknown`). `build_id` is random per build. `manifest` is the **git object id** of each
  rebuild-relevant entry at the repo root — `pyproject.toml`, `Dockerfile`, `compose.yaml` (blob ids) and `models`
  (tree id) — so the same values can be read for the target commit from one API call, without downloading anything
  large. The build computes them (git blob id = sha1 of `blob <len>\0` + content; the `models` tree id is computed from
  the same rule over its entries, or read from `git ls-tree` when the build is run inside a checkout, which the
  documented build command is). Builder's call if a simpler equivalent exists.
- Running version = overlay's `sha` if one is current, else `build-info.commit`. `unknown` is allowed: the banner then
  says "version unknown" and offers the update anyway (the first update establishes a baseline).
- An overlay records the `build_id` it was made against; if the image is rebuilt (different `build_id`) the
  launcher ignores and deletes the stale overlay (the new image already contains newer-or-equal code).
- **Needs rebuild** = the target commit's rebuild-relevant files differ from the image `manifest`. The check runs
  *before* download is offered as applicable; the banner lists which files differ and points to the install guide.
  Because overlays only ever change `smos/`, the manifest of the image stays the reference forever.

### 4. Checking for updates

- Config (env, all optional): `SMOS_UPDATE_REPO` (default: the project repo; empty string disables the feature),
  `SMOS_UPDATE_REF` (default `main`), `SMOS_UPDATE_CHECK_MINUTES` (default 360).
- Background check at startup (delayed) and then at that interval, plus a "Check now" button limited to once per
  minute. `GET https://api.github.com/repos/{repo}/commits/{ref}` for the head SHA; if it differs, one
  `GET .../compare/{running}...{head}` call for the commit count and up to 20 subjects (skipped when running is
  `unknown`). Results cached in memory. Failures (offline, rate limit) show "last check failed: reason", never
  break the dashboard. The rebuild check is one `GET .../git/trees/{sha}` (non-recursive root tree) compared with the image manifest.
- Banner states: up to date · N commits behind (+ subjects) · needs rebuild (+ files) · check failed · update
  running · last update failed/rolled back (+ reason) · feature disabled.

### 5. Applying an update

Admin-only `POST /admin/update/apply` (CSRF, confirmation page showing current SHA → target SHA and the commit
subjects; the form carries the target SHA, and the server re-checks that it equals the *freshly fetched* branch head,
else asks the admin to confirm again).

In the app:
1. Refuse if: feature disabled, needs-rebuild, update already running, or free space in `/data` is less than the
   size of the data files plus 20 % (snapshot needs room).
2. Download `https://github.com/{repo}/archive/{sha}.tar.gz` (the **full 40-hex SHA**, never the branch name) to a
   temp file with a size cap (default 100 MB) and timeout; stream-extract only `*/smos/**` into
   `updates/staging-<sha>/smos`. Reject: absolute or `..` paths, symlinks/hardlinks/devices, files outside `smos/`,
   more than a file-count cap, any single file over a size cap, wrong top-level directory name (must contain the SHA).
3. Smoke-check the staged code with a subprocess: `python -c "import smos ..."` under the staged path to catch
   syntax/import errors before touching anything (does not start the server or open the data).
4. Write `pending.json`, write an audit row (see §8), enter maintenance (writes get 503 `Retry-After`), wait up to
   30 s for in-flight requests, then exit with code 75.

In the launcher, on seeing `pending.json`:
5. Snapshot: copy `metadata.db`, `admin.db`, `faiss.index` (and any other file in the data dir the app owns, excluding
   `updates/`) into `updates/snapshot/` (replacing the previous snapshot). The app is stopped, so files are quiescent.
6. Move staging to `overlay/<sha>`, write the new `current.json` (previous kept in `current.json.prev`).
7. Start the child; poll `/api/v1/health` until healthy or 120 s timeout (the embedding model warm-up counts).
8. Healthy → write `last-result.json` ok, delete `pending.json`, prune old overlays (keep current + previous).
   Not healthy, or child exits early → **rollback**: kill the child; restore the snapshot files over the data dir;
   restore `current.json.prev`; start the child again; write `last-result.json` with the failure and last lines of the
   child's output; wait for health again. If even the rollback does not come up healthy the launcher exits non-zero
   and leaves everything on disk for inspection (Docker will restart it; the result file explains why).

### 6. Security

The update path is remote code execution by design; the controls are:

- Only the repo and ref from container env config; nothing from a request selects them. SHA is taken from GitHub,
  validated as 40 hex chars, and the archive is requested by that SHA.
- Trust root is GitHub over TLS plus the repo owner's account. A compromised owner account means compromised
  installations; v1 does not add commit signing. This is stated in the install guide.
- Admin-only, CSRF-protected, explicit confirmation, one update at a time, audit row for every attempt (who, from/to,
  outcome). The admin session needs no new privilege level.
- Unpack hardening as in §5.2; only the `smos/` package is installed, never scripts, `pyproject.toml`, or hooks;
  extraction never runs any of the downloaded code except the import smoke check (which is code the update would run
  anyway, executed with `-I` and no data access).
- Update client TLS: verify certificates always; extra CA bundle only adds trust anchors; no `verify=False` anywhere.
- Rate limits: "Check now" once per minute; apply once per 5 minutes after a failure.

### 7. Dashboard

The existing banner slot in `smos/admin/templates/dashboard.html` gets the states in §4, the Check now button, and the
Update button (disabled with the reason when not applicable). A small "Last update" line shows `last-result.json`.
An `/admin/update` page holds the confirmation step and shows progress by polling a status endpoint (the app is
restarting during the swap, so the page must tolerate connection errors and reload until the health returns).

### 8. Audit log

Admin DB migration (next number in `ADMIN_MIGRATIONS`): `update_log(id, at, admin_user, from_sha, to_sha, outcome,
detail)`. The launcher cannot write the app's DB while it is being restored, so it writes outcomes to
`last-result.json` and the app imports that into `update_log` on the next healthy start.

## Left to the builder

- Module layout (suggest `smos/update/` with `config`, `github`, `unpack`, `plan`, `launcher` pieces so each is
  testable alone); HTTP client choice (stdlib `urllib` honours proxy variables; use whatever is already a dependency).
- Exact caps (sizes, file counts), polling intervals, template wording.
- How `/health` exposes `version` (add the running SHA to the health payload if cheap).
- Windows dev behaviour: the launcher need only work in the container; unit tests drive its functions directly.

## Testing approach

Hermetic and offline, like the rest of the suite: a fake GitHub (local HTTP server or injected fetcher) serving a tiny
tarball. Cover: tar hardening (traversal, symlink, wrong top dir, oversize); needs-rebuild detection; version
states; apply refused when disabled/needs rebuild/low disk; launcher happy path (fake child that becomes healthy);
rollback path restores data **and** code (fake child that fails health after "migrating" the snapshot files);
stale overlay discarded after a rebuild; CSRF/admin-only/audit; every new route still behind auth
(`test_every_route_in_schema_is_protected`). A real end-to-end run in Docker against a throwaway repo copy is part of
the verification record (`docs/update-verification.md`), not of the pytest suite.

## Calls I made (for the Governor to check)

1. Launcher as a supervisor process inside the container (not exit-and-let-Docker-restart), so the snapshot, swap
   and rollback happen while the app is stopped and are not racing a restart.
2. Rollback restores **data** as well as code; writes made between snapshot and health-check are lost on rollback.
   Maintenance mode (503 on writes) from the click until restart limits that window to nothing in the success case.
3. Version identity is the commit SHA; first install built without `SMOS_COMMIT` shows "unknown" and still updates.
4. Rebuild detection compares git object ids of `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/` with a
   manifest baked at build; one API call for the target side. Any change to those four triggers "needs rebuild", even a
   comment edit (conservative on purpose).
5. Defaults: feature on, repo = this project's public repo, ref `main`, check every 6 h.
6. One snapshot kept, two overlays kept; snapshot needs 120 % of data size free.
7. Archive by SHA from `github.com/.../archive/`, not the API tarball endpoint (not rate-limited the same way);
   commit/compare metadata from the API.

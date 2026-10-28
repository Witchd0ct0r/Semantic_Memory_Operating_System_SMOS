# SMOS Self-Update — Design (Project 4)

Status: draft for Governor approval (revised: updates follow **releases**, not commits). Date: 2026-10-28.

## Intent

An installed SMOS (Docker, on a machine the Governor can reach but does not want to SSH into for every
change) can update itself to the **newest published release** of the public GitHub repo, from a button on the
dashboard, without rebuilding the image and without losing data. If the new release does not come up healthy,
the installation returns to exactly the state it had before the click. Work in progress on `main` never reaches an
installation; only something the maintainer deliberately publishes as a release does.

## Goals

- Dashboard shows the running version (e.g. `0.2.0`) and the newest release available, with its release notes.
- One admin click applies an update: download, verify, back up, swap code, restart, health-check.
- Automatic rollback (code **and** data) if the new release fails to start or fails its health check.
- Says plainly when a release needs a real rebuild (dependencies, Dockerfile, compose, base image, model) and then
  refuses to apply it, instead of half-applying.
- No Docker socket, no privileged container, no host-side agent required.
- A short maintainer checklist (`docs/RELEASING.md`) so a release is cut correctly.

## Non-goals

- No automatic or scheduled updates. Every update is a click.
- No updating from arbitrary URLs, forks, branches, or commits; no choosing an older release and no downgrade in v1.
- Pre-releases and drafts are never offered.
- No update of dependencies, the base image, the bundled embedding model, or `compose.yaml` — those need a rebuild.
- No signature verification of releases (see Security: the trust root is the repo owner's GitHub account).
- No multi-instance coordination; one container per data volume.

## Anti-goals (a failure even if it "works")

- An update that leaves data and code mismatched (new schema, old code) after a failed start.
- Any request-supplied value (URL, tag, path) influencing what is downloaded or where it is written.
- A release whose code reports a different version than its tag being installed silently.
- Update traffic needing anything the existing deploy does not already have (CA bundle, optional proxy).

## Constraints

- Public repo: `https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS` (no credential handling).
  The repo name is configuration, not hard-coded logic.
- The target network re-signs HTTPS: the update client must trust the extra CA bundle already copied to
  `/opt/smos/certs/ca-bundle.crt` when present, and honour `HTTPS_PROXY` / `NO_PROXY`.
- Unauthenticated GitHub API: 60 requests/hour per IP. Cache and rate-limit checks.
- Migrations are forward-only (`smos/storage/migrations.py`); older code refuses newer data
  (`SchemaTooNewError`). Rollback of code therefore requires restoring data taken before the update.
- Container runs as non-root user `smos`; only `/data` is writable. The code overlay lives in `/data`.
- No company/host names in the repo (standing rule).

## Design

### 1. Version identity: the release tag

- A release is a GitHub Release (not a draft, not a pre-release) whose tag is `vMAJOR.MINOR.PATCH`.
- **Running version** = `smos.__version__` of the code actually loaded (overlay or baked). No build argument needed,
  and there is no "unknown" state.
- **Rule for releases:** the tagged commit's `smos/__init__.py` must contain `__version__ = "MAJOR.MINOR.PATCH"`
  equal to the tag. The updater verifies this after unpacking and refuses a mismatch.
- An update is **offered** only when the latest release's version is strictly greater than the running version
  (PEP 440 comparison). Equal or lower → "up to date". Intermediate releases are skipped; the latest is applied
  directly (migrations run in order, so skipping is safe).
- Tags can be moved, so the checker resolves the tag to a **commit SHA** at check time, shows both on the
  confirmation page, and downloads the archive by that SHA.

### 2. Launcher (the only new always-on piece)

`smos-server` becomes a small supervisor (`smos/launcher.py`, kept tiny and stable because it is part of the image
and cannot be updated by an overlay). It runs the real app as a child process (`python -m smos.api.main`) with the
overlay first on `PYTHONPATH` when a valid overlay exists, forwards SIGTERM, and relaunches the child when it
exits with code **75** ("restart requested"). Any other child exit ends the launcher (Docker's
`restart: unless-stopped` then applies as today).

### 3. On-disk layout (`/data/updates/`)

```
overlay/<version>/smos/...  unpacked code of an applied release (only the smos/ package)
current.json                {version, sha, base_build_id, applied_at}; absent = baked code; previous kept as current.json.prev
pending.json                {version, sha, staged_dir, requested_by, requested_at}  written by the app
snapshot/                   copy of the data files taken just before the swap (one kept)
last-result.json            {ok, from_version, to_version, finished_at, error}  shown on the dashboard
```

Pointer file, not a symlink (portable, atomic via write-temp-then-rename).

### 4. "Needs rebuild"

- The image build writes `/opt/smos/build-info.json`: `{build_id, manifest}`. `build_id` is random per build.
  `manifest` is the **git object id** of each rebuild-relevant entry at the repo root — `pyproject.toml`,
  `Dockerfile`, `compose.yaml` (blob ids) and `models` (tree id). The Dockerfile copies those files into the build
  and a build-time script computes the ids (blob id = sha1 of `blob <len>\0` + content), so no host helper or build
  argument is needed. Builder must verify the computed ids equal `git ls-tree` output and guard against line-ending
  differences (`.gitattributes` with `eol=lf` for these files); a mismatch fails toward "needs rebuild", the safe side.
- For the target release the checker makes one `GET .../git/trees/{sha}` (non-recursive root tree) and compares the same
  four ids. Any difference → **needs rebuild**: the banner lists which entries differ and points to the install guide.
  Conservative on purpose: even a comment edit in the Dockerfile counts.
- An overlay records the `build_id` it was made against; if the image is rebuilt (different `build_id`), the launcher
  ignores and deletes the stale overlay (the new image already contains newer-or-equal code).

### 5. Checking for updates

- Config (env, all optional): `SMOS_UPDATE_REPO` (default: the project repo; empty string disables the feature),
  `SMOS_UPDATE_CHECK_MINUTES` (default 360).
- Background check shortly after startup and then at that interval, plus a "Check now" button limited to once per
  minute. `GET https://api.github.com/repos/{repo}/releases/latest` (GitHub already excludes drafts and pre-releases),
  then resolve the tag to a commit SHA, then the root-tree call of §4. Release notes (the release body) are shown as
  escaped plain text. Results cached in memory. Failures (offline, rate limit) show "last check failed: reason",
  never break the dashboard.
- Banner states: up to date · release X available (+ notes) · needs rebuild (+ entries) · check failed · update running
  · last update failed and was rolled back (+ reason) · feature disabled.

### 6. Applying an update

Admin-only `POST /admin/update/apply` (CSRF; confirmation page showing running version → release version, tag, commit
SHA and the release notes; the form carries the version, and the server re-checks it equals the *freshly fetched* latest
release, else asks the admin to confirm again).

In the app:
1. Refuse if: feature disabled, needs-rebuild, update already running, or free space in `/data` is less than the size of
   the data files plus 20 % (snapshot needs room).
2. Download `https://github.com/{repo}/archive/{sha}.tar.gz` (the full 40-hex SHA, never the tag) to a temp file with a
   size cap (default 100 MB) and timeout; stream-extract only `*/smos/**` into `updates/staging-<version>/smos`.
   Reject: absolute or `..` paths, symlinks/hardlinks/devices, files outside `smos/`, more than a file-count cap, any single file over a
   size cap, wrong top-level directory name (must contain the SHA).
3. Verify `__version__` in the staged `smos/__init__.py` equals the release version (read as text, not imported), then
   smoke-check with a subprocess: `python -I -c "import smos.api.app"` under the staged path (no data access, does not
   start the server) to catch syntax/import errors before anything is touched.
4. Write `pending.json`, write an audit row (§9), enter maintenance (writes get 503 `Retry-After`), wait up to 30 s for
   in-flight requests, then exit with code 75.

In the launcher, on seeing `pending.json`:
5. Snapshot: copy `metadata.db`, `admin.db`, `faiss.index` (and any other file the app owns in the data dir, excluding
   `updates/`) into `updates/snapshot/`, replacing the previous snapshot. The app is stopped, so files are quiescent.
6. Move staging to `overlay/<version>`, write the new `current.json` (previous kept as `.prev`).
7. Start the child; poll `/api/v1/health` until healthy **and reporting the target version** (health payload gains a
   `version` field), or 120 s timeout (embedding warm-up counts).
8. Good → write `last-result.json` ok, delete `pending.json`, prune old overlays (keep current + previous).
   Bad (timeout, early exit, wrong version) → **rollback**: kill the child; restore the snapshot files over the data dir;
   restore `current.json.prev`; start the child again; write `last-result.json` with the failure and the last lines of
   the child's output; wait for health again. If even the rollback does not come up healthy the launcher exits non-zero
   and leaves everything on disk for inspection (Docker restarts it; the result file explains why).

### 7. Security

The update path is remote code execution by design; the controls are:

- Only the repo from container env config; nothing from a request selects it. The SHA is taken from GitHub, validated as 40 hex
  chars, and the archive is requested by that SHA, not the movable tag.
- Trust root is GitHub over TLS plus the repo owner's account. A compromised owner account means compromised
  installations; v1 adds no signing. The install guide says so.
- Admin-only, CSRF-protected, explicit confirmation, one update at a time, an audit row for every attempt.
- Unpack hardening as in §6.2; only the `smos/` package is installed, never scripts, `pyproject.toml` or hooks. The only
  downloaded code run before the swap is the import smoke check (`-I`, no data access).
- Update client TLS: always verify certificates; the extra CA bundle only adds trust anchors; no `verify=False` anywhere.
- Rate limits: "Check now" once per minute; apply retry allowed once per 5 minutes after a failure.

### 8. Dashboard

The existing banner slot in `smos/admin/templates/dashboard.html` gets the states of §5, the Check now button and the
Update button (disabled with the reason when not applicable). A "Last update" line shows `last-result.json`. An
`/admin/update` page holds the confirmation step and shows progress by polling a status endpoint (the app is
restarting during the swap, so the page must tolerate connection errors and reload until health returns).

### 9. Audit log

Admin DB migration (next number in `ADMIN_MIGRATIONS`): `update_log(id, at, admin_user, from_version, to_version,
sha, outcome, detail)`. The launcher cannot write the app's DB while it is being restored, so it writes outcomes to
`last-result.json` and the app imports that into `update_log` on the next healthy start.

### 10. Releasing (`docs/RELEASING.md`)

Short checklist: bump `__version__` (and `pyproject.toml` `version`) on `main`; tests green; if dependencies, Dockerfile,
compose or models changed, say in the notes that installations must rebuild; create a GitHub Release `vX.Y.Z` on that commit,
not a pre-release, notes written for operators. Never move a published tag; publish a new patch release instead.
`tests/` gets a check that `smos.__version__` equals the `pyproject.toml` version so the two cannot drift.

## Left to the builder

- Module layout (suggest `smos/update/` with `config`, `github`, `unpack`, `plan`, `launcher` pieces so each is
  testable alone); HTTP client choice (stdlib `urllib` honours proxy variables; use what is already a dependency).
- Exact caps (sizes, file counts), polling intervals, template wording.
- Windows dev behaviour: the launcher need only work in the container; unit tests drive its functions directly.

## Testing approach

Hermetic and offline, like the rest of the suite: a fake GitHub (local HTTP server or injected fetcher) serving release
JSON, a tree listing and a tiny tarball. Cover: release selection (draft/pre-release ignored, equal/lower version means
up to date, newest wins); tag/`__version__` mismatch refused; tar hardening (traversal, symlink, wrong top dir,
oversize); needs-rebuild detection; apply refused when disabled/needs rebuild/low disk; launcher happy path (fake child
that becomes healthy with the target version); rollback restores data **and** code (fake child that fails health after
"migrating" the snapshot files, and one that reports the wrong version); stale overlay discarded after a rebuild;
CSRF/admin-only/audit; every new route still behind auth (`test_every_route_in_schema_is_protected`);
version-sync test. A real end-to-end run in Docker against a throwaway repo with two releases is part of the
verification record (`docs/update-verification.md`), not the pytest suite.

## Calls I made (for the Governor to check)

1. Launcher as a supervisor inside the container (not exit-and-let-Docker-restart), so snapshot, swap and rollback
   happen while the app is stopped.
2. Rollback restores **data** as well as code; writes between snapshot and health check would be lost, but writes are
   refused (503) from the click until restart, so a successful update loses nothing.
3. Latest release only, never a downgrade, pre-releases ignored; the running version is `smos.__version__` itself.
4. Release tags are treated as movable: the archive is fetched by the resolved commit SHA.
5. Rebuild detection compares git object ids of `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/` computed at image
   build; any change to those four triggers "needs rebuild", even a comment edit.
6. Defaults: feature on, repo = this project's public repo, check every 6 h; one snapshot kept, two overlays kept;
   snapshot needs 120 % of the data size free.
7. Archive from `github.com/.../archive/<sha>.tar.gz`; release, tag and tree metadata from the API.

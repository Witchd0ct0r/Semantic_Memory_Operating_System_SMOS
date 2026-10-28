# Self-Update (Project 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan inline, task by task (the Governor chose inline execution; no subagents). Steps use checkbox (`- [ ]`) syntax.

**Goal:** An admin can update an installed SMOS container to the newest GitHub Release from the dashboard, with data+code snapshot and automatic rollback.

**Architecture:** `smos-server` becomes a stdlib-only supervisor (`smos/launcher.py`) that runs the app as a child, restarts it on exit code 75, and applies a staged release overlay from `/data/updates/` with snapshot, health check and rollback. The app side (`smos/update/`) checks GitHub Releases, stages and verifies a release archive, then asks the launcher to swap by writing `pending.json` and exiting 75. Admin routes and the dashboard banner drive it.

**Tech Stack:** Python 3.12 stdlib (`urllib`, `tarfile`, `ssl`, `subprocess`), FastAPI/Jinja2 (existing), SQLite admin DB (migration 4), pytest with a local fake GitHub.

**Spec:** `docs/superpowers/specs/2026-10-28-self-update-design.md` (read it first; this plan only records decisions it leaves open).

## Global Constraints

- Release tags are exactly `vMAJOR.MINOR.PATCH`; the running version is `smos.__version__`; only a strictly greater version is offered; drafts and pre-releases never.
- Archive is fetched by the resolved full 40-hex commit SHA, never the tag; only `smos/**` is extracted, via our own writer (never `tarfile.extract`).
- No request value selects repo, URL, tag or path. No `verify=False` anywhere; the CA bundle `/opt/smos/certs/ca-bundle.crt` only adds trust anchors; `HTTPS_PROXY`/`NO_PROXY` honoured.
- `smos/launcher.py` imports only the standard library. Everything else under `smos/update/` may use project modules.
- Rebuild list (git object ids): `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models` (tree), `smos/launcher.py`.
- Exit code 75 = "restart requested". Health wait 120 s. Drain wait 30 s. Check-now limit 1/min; retry after failure 5 min. Snapshot needs data size x 1.2 + archive cap free.
- Admin routes: `admin_required` + CSRF on every POST. Dashboard stays 200 when GitHub is unreachable.
- Test commands: `rtk proxy python -m pytest <path> -q` (plain `pytest` may report "No tests collected"). Suite baseline is 527 passed; tests are hermetic and offline.
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Keep employer, CA and host names out of the repo (standing rule).

## Review Focus

1. **GitHub has no release yet / 404 / 403 rate limit / invalid JSON / HTML error page** → status `check_failed` or `up_to_date`, dashboard still 200 (Task 6, 8, 10). The repo has no releases today.
2. **Odd tags and versions** (`v0.3`, `0.3.0`, `v1.0.0-rc1`, `vX`, whitespace) → never offered (Task 4, 6).
3. **Hostile release notes** (`<script>`, 1 MB of text) → escaped and truncated on the page (Task 6, 10).
4. **Crash or restart of the launcher mid-update** (pending.json present, snapshot complete, overlay already switched) → never re-snapshots migrated data; ends healthy-new or rolled back (Task 3).
5. **Double click / two admins applying at once** → exactly one update runs, the other gets a refusal (Task 9, 10).

---

### Task 1: Version plumbing and repo hygiene

**Files:**
- Modify: `smos/api/app.py` (`_version`, drop `importlib.metadata` import), `.gitattributes`
- Create: `tests/test_versioning.py`

**Interfaces:**
- Produces: `_version()` returns `smos.__version__` (the overlay's code reports its own version; installed-dist metadata would not).

- [ ] **Step 1: Write failing tests** in `tests/test_versioning.py`:
  `test_pyproject_version_equals_package_version` (tomllib read of `pyproject.toml` `project.version` == `smos.__version__`);
  `test_health_reports_the_loaded_code_version(api)` (monkeypatch `smos.__version__` to `"9.9.9"`; `/api/v1/health` json `version == "9.9.9"`);
  `test_gitattributes_pins_lf_for_rebuild_files` (`.gitattributes` has lines containing `Dockerfile`, `compose.yaml`, `pyproject.toml`, `smos/launcher.py` each with `eol=lf`).
- [ ] **Step 2:** Run `rtk proxy python -m pytest tests/test_versioning.py -q` → FAIL (health test and gitattributes test).
- [ ] **Step 3:** `_version()` returns `smos.__version__` read at call time (`import smos` then `smos.__version__`, so the monkeypatch works); remove the `metadata` import. Add the four `text eol=lf` lines to `.gitattributes`.
- [ ] **Step 4:** Run the file → PASS; run `rtk proxy python -m pytest tests/test_api_auth.py tests/test_dashboard_page.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(update): report the loaded code's version; pin LF for rebuild files`.

### Task 2: Launcher state, overlay resolution, snapshot/restore

**Files:**
- Create: `smos/launcher.py`, `tests/test_launcher_state.py`

**Interfaces:**
- Produces (all in `smos/launcher.py`, stdlib only):
  - `EXIT_RESTART = 75`
  - `class UpdatePaths(data_dir: Path)` with attributes `root` (`data_dir/"updates"`), `current`, `current_prev`, `pending`, `last_result`, `snapshot` (all `Path`) and methods `overlay_dir(version: str) -> Path`, `staging_dir(version: str) -> Path`.
  - `read_json(path: Path) -> dict | None` (None when missing or unparseable), `write_json_atomic(path: Path, obj: dict) -> None` (temp file + `os.replace`).
  - `load_build_info() -> dict` (path from env `SMOS_BUILD_INFO`, default `/opt/smos/build-info.json`; `{}` when missing).
  - `active_overlay(paths: UpdatePaths, build_id: str | None) -> Path | None`: returns `overlay_dir(version)` when `current.json` exists, its `base_build_id == build_id` and the directory exists; otherwise returns None and, if `current.json` existed but is stale/broken, deletes it and the overlay tree.
  - `snapshot_data(data_dir: Path, paths: UpdatePaths, token: str) -> None`: replaces `paths.snapshot` with a copy of everything in `data_dir` except `updates/`, then writes `paths.snapshot/".complete"` containing `token`.
  - `snapshot_matches(paths: UpdatePaths, token: str) -> bool`.
  - `restore_data(data_dir: Path, paths: UpdatePaths) -> None`: deletes everything in `data_dir` except `updates/`, copies the snapshot back (not `.complete`).

- [ ] **Step 1: Write failing tests**: `test_active_overlay_returns_current_when_build_matches`; `test_active_overlay_none_and_cleaned_when_build_id_differs` (overlay dir and `current.json` gone); `test_active_overlay_none_when_overlay_dir_missing`; `test_snapshot_copies_all_but_updates_and_marks_complete` (nested dir, `metadata.db`, `faiss.index`, an `updates/x` that must not be copied; `.complete` holds the token); `test_snapshot_replaces_previous_snapshot`; `test_restore_removes_files_created_after_snapshot_and_keeps_updates`; `test_write_json_atomic_roundtrip_and_read_json_tolerates_garbage`.
- [ ] **Step 2:** Run `rtk proxy python -m pytest tests/test_launcher_state.py -q` → FAIL (import).
- [ ] **Step 3:** Implement the signatures above in `smos/launcher.py` (module docstring: stdlib only, frozen formats, any change to this file requires an image rebuild).
- [ ] **Step 4:** Run the file → PASS.
- [ ] **Step 5:** Commit `feat(update): launcher state files, overlay resolution, data snapshot`.

### Task 3: Launcher supervisor: restart loop, apply, health, rollback

**Files:**
- Modify: `smos/launcher.py`, `pyproject.toml` (`smos-server = "smos.launcher:main"`), `tests/test_packaging.py` and any test asserting the old entry point
- Create: `tests/test_launcher_supervisor.py`

**Interfaces:**
- Consumes: Task 2 helpers.
- Produces:
  - `class Child(Protocol)`: `poll() -> int | None`, `wait(timeout: float | None = None) -> int`, `terminate() -> None`, `kill() -> None`, `tail() -> list[str]`.
  - `class Supervisor(data_dir: Path, *, spawn: Callable[[Path | None], Child], health: Callable[[str | None], bool], build_id: str | None, clock=time.monotonic, sleep=time.sleep, health_timeout: float = 120.0, poll_interval: float = 1.0)`; `run() -> int` returns the final child exit code (non-zero if rollback could not recover); `request_stop() -> None` terminates the child and makes `run` return.
  - `main() -> None`: builds the real `spawn` (child `sys.executable -m smos.api.main`, `PYTHONPATH` = overlay path + existing, stdout/stderr relayed line by line to our stdout while keeping the last 40 lines for `tail()`), real `health` (GET `http://127.0.0.1:$SMOS_PORT/api/v1/health`, JSON `status == "ok"` and, when a version is given, `version ==` it), signal handlers for SIGTERM/SIGINT calling `request_stop`, then `sys.exit(Supervisor(...).run())`.
- Behaviour of `run()`: loop { if `pending.json` exists run the apply procedure; else spawn with `active_overlay`; wait; exit 75 → loop, otherwise return code }.
  Apply procedure (idempotent): token = `f"{pending['version']}@{pending['requested_at']}"`; if staging dir missing and overlay for the version missing → write failed `last-result.json`, delete `pending.json`, continue normally; if `not snapshot_matches` → `snapshot_data`; move staging to `overlay_dir(version)` if staging still exists; if `current.json` names another version copy it to `current.json.prev` (or leave no prev when absent), write new `current.json` `{version, sha, base_build_id, applied_at}`; spawn; poll `health(version)` until true, child exits, or `health_timeout`; success → `last-result.json` `{ok: true, from_version, to_version, requested_by, requested_at, finished_at}`, delete `pending.json`, prune overlays other than current and previous, return the live child; failure → terminate child, `restore_data`, restore `current.json` from `.prev` (delete it when there was none), spawn with `active_overlay`, require `health(None)`, write `last-result.json` with `ok: false`, `error`, `log_tail` (child's last lines), delete `pending.json`; if the rollback child is also unhealthy → return a non-zero code and leave `pending.json`, snapshot and files in place.

- [ ] **Step 1: Write failing tests** with a `FakeChild` and scripted `spawn`/`health`/fake clock:
  `test_plain_start_passes_no_overlay_and_returns_exit_code`; `test_exit_75_restarts_then_returns_next_exit_code`; `test_stale_overlay_is_ignored_on_start`;
  `test_pending_update_success` (overlay moved into place, `current.json` has version/sha/base_build_id, `last-result.json` ok, `pending.json` gone, snapshot exists, child spawned with the overlay path);
  `test_unhealthy_new_code_rolls_back_code_and_data` (the fake child "migrates" by overwriting `metadata.db` and adding `new.db` when spawned with the overlay; afterwards original content restored, `new.db` gone, `current.json` absent, second spawn has overlay None, result `ok: false` with `log_tail`);
  `test_wrong_reported_version_rolls_back`; `test_child_exiting_early_rolls_back`; `test_health_timeout_uses_clock_not_wall_time`;
  `test_rollback_restores_previous_overlay_when_there_was_one` (`current.json.prev` honoured);
  `test_rollback_failure_returns_nonzero_and_keeps_files`;
  `test_restart_with_pending_and_complete_snapshot_does_not_resnapshot` (Review Focus 4: snapshot taken, overlay switched, data already "migrated", then a new `Supervisor.run()` with the child still unhealthy → data restored from the ORIGINAL snapshot);
  `test_pending_without_staging_or_overlay_is_recorded_as_failed_and_start_continues`;
  `test_request_stop_terminates_child_and_run_returns`.
- [ ] **Step 2:** Run `rtk proxy python -m pytest tests/test_launcher_supervisor.py -q` → FAIL.
- [ ] **Step 3:** Implement `Supervisor` and `main` as specified. Update the `pyproject.toml` entry point and the packaging test that names it.
- [ ] **Step 4:** Run `tests/test_launcher_supervisor.py tests/test_launcher_state.py tests/test_packaging.py tests/test_docker_files.py` → PASS.
- [ ] **Step 5:** Commit `feat(update): supervisor with apply, health check and rollback`.

### Task 4: Update config and version rules

**Files:**
- Create: `smos/update/__init__.py`, `smos/update/config.py`, `smos/update/versions.py`, `tests/test_update_config.py`

**Interfaces:**
- Produces:
  - `versions.py`: `Version = tuple[int, int, int]`; `parse_tag(tag: str) -> Version | None` (only `^v(\d+)\.(\d+)\.(\d+)$`, no whitespace); `parse_version(text: str) -> Version | None` (`^(\d+)\.(\d+)\.(\d+)$`); `is_newer(candidate: Version, running: Version | None) -> bool` (None running counts as `(0,0,0)`); `format_version(v: Version) -> str`.
  - `config.py`: `DEFAULT_REPO = "Witchd0ct0r/Semantic_Memory_Operating_System_SMOS"`; frozen `UpdateConfig(repo: str, check_minutes: int = 360, api_base: str = "https://api.github.com", archive_base: str = "https://github.com", ca_bundle: Path | None = Path("/opt/smos/certs/ca-bundle.crt"), max_archive_bytes: int = 100*1024*1024, max_total_bytes: int = 300*1024*1024, max_files: int = 5000, max_file_bytes: int = 20*1024*1024, problem: str | None = None)` with property `enabled` (`bool(repo) and problem is None`) and classmethod `from_env(environ: Mapping[str, str] = os.environ) -> UpdateConfig`.
- Rules for `from_env`: `SMOS_UPDATE_REPO` unset → default, empty → disabled, otherwise must match `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` (else disabled with `problem`); `SMOS_UPDATE_CHECK_MINUTES` integer 5..10080 (else default + no problem); `SMOS_UPDATE_API_BASE` / `SMOS_UPDATE_ARCHIVE_BASE` (test hooks, operator-only) accepted only when `https://` or `http://` with host `localhost`, `127.0.0.1` or `host.docker.internal`, else `problem`.

- [ ] **Step 1: Write failing tests**: `test_parse_tag_accepts_only_vx_y_z` (accepts `v0.3.0`, `v10.20.30`; rejects `v0.3`, `0.3.0`, `v1.0.0-rc1`, `v1.0.0 `, `vX`, `""`); `test_is_newer_is_strict`; `test_from_env_defaults`; `test_empty_repo_disables`; `test_bad_repo_sets_problem_and_disables`; `test_check_minutes_bounds`; `test_base_overrides_require_https_or_loopback`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(update): update config and strict release-version rules`.

### Task 5: Build manifest (git object ids) and Dockerfile

**Files:**
- Create: `smos/update/manifest.py`, `tests/test_update_manifest.py`
- Modify: `Dockerfile`, `tests/test_docker_files.py`

**Interfaces:**
- Consumes: `smos.launcher.load_build_info`.
- Produces: `REBUILD_PATHS = ("pyproject.toml", "Dockerfile", "compose.yaml", "models", "smos/launcher.py")`; `blob_id(data: bytes) -> str`; `file_id(path: Path) -> str`; `tree_id(directory: Path) -> str` (git tree format, recursive, files mode `100644`, directories `40000`, git's sort rule where a directory sorts as `name/`, empty directories skipped); `build_manifest(entries: Mapping[str, Path]) -> dict[str, str]`; `differing(image: Mapping[str, str], target: Mapping[str, str]) -> list[str]` (sorted repo paths that differ or are missing from the target); `write_build_info(out: Path, entries: Mapping[str, Path]) -> dict` (writes `{"build_id": uuid4 hex, "manifest": {...}}`); CLI `python -m smos.update.manifest --out FILE REPO_PATH=LOCAL_PATH ...`.

- [ ] **Step 1: Write failing tests**: `test_blob_id_matches_git` (`blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"`); `test_tree_id_matches_git_write_tree` (temp dir with nested files, run real `git init/add/write-tree`, skip when git is missing); `test_repo_rebuild_files_match_git_head` (skip unless `git status --porcelain` is empty for the five paths; compare each `build_manifest` id with `git rev-parse HEAD:<path>`; this is the guard against line-ending and `.dockerignore` drift); `test_differing_reports_changed_and_missing`; `test_write_build_info_has_random_build_id_and_manifest`; in `test_docker_files.py`: `test_dockerfile_writes_build_info_with_all_rebuild_paths` (Dockerfile contains `COPY pyproject.toml Dockerfile compose.yaml`, `python -m smos.update.manifest`, and every name in `REBUILD_PATHS`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement the module. In the `Dockerfile`, after `COPY models`, add the `COPY pyproject.toml Dockerfile compose.yaml /opt/smos/build-files/` and one `RUN python -m smos.update.manifest --out /opt/smos/build-info.json ...` (root, before `USER smos`) mapping each repo path to its in-image location (`/opt/smos/src/smos/launcher.py` for the launcher, `/opt/smos/models` for `models`).
- [ ] **Step 4:** Run `tests/test_update_manifest.py tests/test_docker_files.py` → PASS (the git-head test may skip while the Dockerfile edit is uncommitted; rerun after the commit and expect a real pass).
- [ ] **Step 5:** Commit, then rerun the git-head test and expect PASS.

### Task 6: GitHub client

**Files:**
- Create: `smos/update/github.py`, `tests/fake_github.py` (local `http.server` on 127.0.0.1 serving scripted routes), `tests/test_update_github.py`

**Interfaces:**
- Consumes: `UpdateConfig`, `versions.parse_tag`, `manifest.REBUILD_PATHS`.
- Produces: `class GitHubError(Exception)` (message safe to show an admin); frozen `Release(version: Version, tag: str, notes: str, url: str)`; `build_ssl_context(config: UpdateConfig) -> ssl.SSLContext` (default context, `load_verify_locations(ca_bundle)` added only when the file exists); `class GitHubClient(config: UpdateConfig, opener: urllib.request.OpenerDirector | None = None)` with
  `latest_release() -> Release | None` (404 → None; tag not `vX.Y.Z` → `GitHubError`; notes truncated to 20 000 chars; JSON body capped at 1 MB);
  `resolve_tag(tag: str) -> str` (40-hex sha via `GET /repos/{repo}/commits/{tag}`; anything else → `GitHubError`);
  `target_manifest(sha: str) -> dict[str, str]` (root tree for `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models`, then the `smos` subtree for `launcher.py`; keys use `REBUILD_PATHS` spelling; a missing entry is simply absent);
  `download_archive(sha: str, dest: Path) -> None` (`{archive_base}/{repo}/archive/{sha}.tar.gz`, streamed, aborts past `max_archive_bytes`, redirects followed only to https URLs or same-origin loopback).
  Errors: 403/429 with `X-RateLimit-Remaining: 0` → "GitHub rate limit reached, try later"; connection failures → "cannot reach GitHub: <reason>"; non-JSON → "unexpected response from GitHub".

- [ ] **Step 1: Write failing tests** against the fake server (opener built with `ProxyHandler({})`): `test_latest_release_parses_tag_notes_url`; `test_no_release_returns_none`; `test_non_semver_tag_is_an_error`; `test_rate_limit_message`; `test_invalid_json_and_html_error_page_are_errors`; `test_connection_refused_is_an_error_not_an_exception_leak`; `test_notes_are_truncated`; `test_resolve_tag_requires_40_hex`; `test_target_manifest_reads_root_and_smos_subtree`; `test_download_enforces_size_cap_and_cleans_partial_file`; `test_download_refuses_redirect_to_plain_http_elsewhere`; `test_request_urls_use_only_configured_repo` (fake server records paths); `test_ssl_context_verifies_and_adds_bundle_only_when_present` (verify_mode `CERT_REQUIRED`, `check_hostname` true; monkeypatched `load_verify_locations` called once with the path or not at all).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with stdlib `urllib` (User-Agent `smos-updater`, 15 s timeout, `Accept: application/vnd.github+json`; default opener honours `HTTPS_PROXY`/`NO_PROXY`). **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(update): GitHub release client`.

### Task 7: Archive extraction and smoke check

**Files:**
- Create: `smos/update/unpack.py`, `tests/test_update_unpack.py`

**Interfaces:**
- Consumes: `UpdateConfig` limits, `versions.parse_version`.
- Produces: `class UnpackError(Exception)`; `ALLOWED_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".txt", ".md"}`;
  `extract_smos(archive: Path, sha: str, dest: Path, config: UpdateConfig) -> str` (writes `dest/smos/**` with our own file writer; returns the `__version__` string read as text from `smos/__init__.py`);
  `smoke_check(staged: Path, module: str = "smos.api.app", timeout: float = 90.0) -> None` (subprocess `sys.executable -I -c` that inserts `staged` at `sys.path[0]`, imports `module`, and asserts the module file lives under `staged`; failure → `UnpackError` with the last stderr lines).
- Reject with `UnpackError`: absolute or `..` member names anywhere; symlink, hardlink, device, fifo members under `smos/`; top-level directory not ending in `-<sha>`; missing `smos/__init__.py` or `__version__`; more than `max_files`; a file over `max_file_bytes`; total over `max_total_bytes`; suffix outside `ALLOWED_SUFFIXES`. Members outside `smos/` are ignored. On any error `dest` is removed.

- [ ] **Step 1: Write failing tests** (build tarballs in memory with `tarfile`): `test_extracts_smos_only_and_returns_version`; `test_path_traversal_rejected`; `test_absolute_path_rejected`; `test_symlink_hardlink_and_device_rejected`; `test_wrong_top_directory_rejected`; `test_missing_version_rejected`; `test_file_count_size_and_total_caps`; `test_disallowed_suffix_rejected`; `test_error_leaves_no_dest_directory`; `test_smoke_check_passes_for_importable_package` and `test_smoke_check_reports_syntax_error` (tiny staged package with a module name parameter, no heavy imports).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(update): hardened archive extraction and import smoke check`.

### Task 8: Update status and checker

**Files:**
- Create: `smos/update/status.py`, `tests/test_update_status.py`

**Interfaces:**
- Consumes: `GitHubClient`, `manifest.differing`, `versions`.
- Produces: frozen `UpdateStatus(state: str, running: str, checked_at: float | None, release: Release | None = None, sha: str | None = None, rebuild_files: tuple[str, ...] = (), error: str | None = None)` with `state` in `{"unchecked", "up_to_date", "available", "needs_rebuild", "check_failed"}`; `evaluate(client, running_version: str, image_manifest: Mapping[str, str], now: float) -> UpdateStatus` (never raises: `GitHubError` → `check_failed`); `class UpdateChecker(config, client, running_version: str, image_manifest: Mapping[str, str], clock=time.time)` with `status() -> UpdateStatus` (cached, starts `unchecked`), `check(force: bool = False) -> UpdateStatus` (forced checks refuse to hit GitHub more than once per 60 s and return the cache; non-forced only when older than `check_minutes`), `retry_after() -> int` (seconds until a forced check is allowed), `start(first_delay: float = 30.0)` / `stop()` (daemon thread loop).
- Logic: latest None or not newer → `up_to_date`; else resolve sha, fetch target manifest, `differing` non-empty → `needs_rebuild` with those paths, else `available`.

- [ ] **Step 1: Write failing tests** with a fake client object: `test_no_release_is_up_to_date`; `test_same_or_older_release_is_up_to_date`; `test_newer_release_available_with_sha`; `test_changed_dockerfile_needs_rebuild_and_lists_it`; `test_changed_launcher_needs_rebuild`; `test_github_error_becomes_check_failed_with_message`; `test_check_now_is_rate_limited_to_once_a_minute` (fake clock); `test_background_loop_stops_cleanly`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(update): update status evaluation and cached checker`.

### Task 9: Apply orchestration, maintenance mode, audit log

**Files:**
- Create: `smos/update/apply.py`, `smos/update/audit.py`, `smos/update/service.py`, `tests/test_update_apply.py`, `tests/test_update_audit.py`
- Modify: `smos/admin/db.py` (migration 4), `smos/api/app.py` (maintenance middleware, in-flight counter, `app.state.update`, import of last result), `smos/api/deps.py` (`require_write` returns 503 in maintenance), `tests/test_metrics.py:171` (expect `ADMIN_MIGRATIONS[-1][0]` instead of `3`)

**Interfaces:**
- Consumes: Tasks 2-8.
- Produces:
  - Migration 4 `update_log`: `id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, admin_user TEXT NOT NULL, from_version TEXT NOT NULL, to_version TEXT NOT NULL, sha TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''`.
  - `audit.py`: `record(conn, *, admin_user, from_version, to_version, sha, outcome, detail="", at=None) -> None`; `recent(conn, limit: int = 10) -> list[dict]`; `import_last_result(conn, paths: UpdatePaths) -> bool` (inserts one row `applied` or `rolled_back` from `last-result.json`, idempotent on `at == finished_at`; carries `requested_by`).
  - `apply.py`: `class ApplyRefused(Exception)`; `class Updater(app, config, client, checker, paths, *, running_version, exit_fn=os._exit, exit_delay: float = 1.0, drain_timeout: float = 30.0, clock=time.monotonic)` with `begin(admin: str, expected_version: str) -> None` and `state() -> str` (`"idle" | "staging" | "restarting"`).
  - `begin` order: non-blocking busy lock (else `ApplyRefused("an update is already running")`); cooldown 5 min after a failed attempt; feature enabled; a **fresh** `evaluate` (not the cache) must be `available` and its release version must equal `expected_version` (else `ApplyRefused` asking to review again; `needs_rebuild` refuses naming the files); free space check; download to `updates/download-<v>.tar.gz`; `extract_smos` into `staging_dir(v)`; staged `__version__` must equal the release version; `smoke_check`; write `pending.json` `{version, sha, requested_by, requested_at, from_version}`; set `app.state.maintenance = True`; start the exit thread (sleep `exit_delay`, wait until `app.state.inflight <= 1` or `drain_timeout`, `app.state.close()`, `exit_fn(75)`). Any failure before `pending.json` removes download and staging, clears maintenance, records an audit row (`refused` / `failed`) and starts the cooldown for staging failures.
  - `service.py`: `@dataclass UpdateService(config, checker, updater, disabled_reason)`; `attach(app, data_dir) -> None` (uses `UpdateConfig.from_env`, `load_build_info`; disabled with a reason when the repo is empty, the config has a `problem`, or no build info exists; otherwise builds client, checker, updater and starts the checker); `describe(app) -> dict` for templates: `{enabled, reason, state, running, release_version, tag, notes, url, sha, rebuild_files, error, can_apply, retry_after, updater_state, last_result}`.
  - `app.py`: `app.state.maintenance = False`, `app.state.inflight = 0`; an http middleware increments/decrements `inflight` in `try/finally` and, when `maintenance` is set, answers non-GET/HEAD requests under `/admin` and `/claim` with 503 + `Retry-After: 30`; `require_write` raises 503 (`Retry-After`) in maintenance before the scope check passes; `create_app` calls `audit.import_last_result` (errors logged, never fatal) and `service.attach` at the end.

- [ ] **Step 1: Write failing tests**: audit - `test_migration_4_creates_update_log_and_keeps_rows`, `test_import_last_result_inserts_once`, `test_import_after_rollback_still_records_admin_and_error`. Apply (fake client serving a tiny valid tarball, `exit_fn` recorder, `exit_delay=0`) - `test_begin_stages_writes_pending_sets_maintenance_and_exits_75`; `test_refuses_when_disabled`; `test_refuses_needs_rebuild_naming_files`; `test_refuses_when_latest_changed_since_confirmation`; `test_refuses_on_low_disk` (monkeypatched `shutil.disk_usage`); `test_refuses_when_staged_version_differs_from_tag`; `test_smoke_failure_cleans_up_and_clears_maintenance`; `test_second_concurrent_begin_is_refused` (Review Focus 5: second call from another thread while the first is blocked inside download); `test_cooldown_after_failed_staging`; `test_exit_waits_for_inflight_requests_up_to_drain_timeout`. Maintenance - `test_writes_get_503_while_reads_work` (API write route with rw token → 503 + Retry-After, `POST /api/v1/query` still 200, admin POST → 503, `GET /admin/dashboard` still 200); `test_inflight_counter_returns_to_zero`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. Update `tests/test_metrics.py` expectation. **Step 4:** Run the new files, `tests/test_metrics.py`, `tests/test_api_auth.py`, `tests/test_api_scopes.py`, `tests/test_migrations.py` → PASS.
- [ ] **Step 5:** Commit `feat(update): staged apply with maintenance mode and audit log`.

### Task 10: Admin routes, dashboard banner, update page

**Files:**
- Modify: `smos/admin/pages.py`, `smos/admin/dashboard_view.py`, `smos/admin/templates/dashboard.html`, `tests/test_dashboard_page.py` (banner text assertion)
- Create: `smos/admin/templates/update.html`, `tests/test_update_pages.py`

**Interfaces:**
- Consumes: `service.describe(app)`, `app.state.update` (`UpdateService`), `Updater.begin`, `ApplyRefused`.
- Produces routes (all `include_in_schema=False`, `admin_required`): `GET /admin/update` (status, release notes as escaped text, confirmation form with `csrf` and hidden `version`, progress area); `POST /admin/update/check` (CSRF; forced check, 303 to `/admin/dashboard`); `POST /admin/update/apply` (CSRF; success 303 to `/admin/update?applying=1`; `ApplyRefused` → page with the message, status 409); `GET /admin/update/status` (JSON `{state, version, last_result}`). `build_dashboard` adds `"update": describe(app)`; the banner shows running version and one state line (up to date / release X available with link to `/admin/update` / needs rebuild + files / check failed + reason / disabled + reason / last update rolled back + reason), a Check now button, and a "Last update" line. `update.html` carries a small inline script that, while `applying=1` or `updater_state == "restarting"`, polls `/admin/update/status` every 2 s tolerating fetch errors and reloads when the version changed or `last_result` appears.

- [ ] **Step 1: Write failing tests** (inject a fake `UpdateService` onto `app.state.update`; `_login` helper as in `test_dashboard_page.py`): `test_dashboard_shows_each_banner_state` (parametrized over the five states plus disabled); `test_dashboard_is_200_when_check_failed`; `test_release_notes_are_escaped_and_not_executable` (Review Focus 3: `<script>alert(1)</script>` appears escaped, raw tag absent); `test_update_routes_require_admin_login` (GET and POST all redirect/401 without a session); `test_post_routes_reject_missing_or_wrong_csrf`; `test_check_now_calls_forced_check`; `test_apply_calls_begin_with_admin_and_version_then_redirects`; `test_apply_refusal_renders_message_with_409`; `test_status_endpoint_json_shape`; `test_confirmation_page_shows_version_tag_and_sha`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; replace the "update checking arrives with the updater" text. **Step 4:** Run the new file, `tests/test_dashboard_page.py`, `tests/test_admin_pages.py` → PASS.
- [ ] **Step 5:** Commit `feat(update): dashboard banner and admin update page`.

### Task 11: Documentation, configuration and spec amendments

**Files:**
- Create: `docs/RELEASING.md`
- Modify: `docs/INSTALL.md` (new section "Updating"), `.env.example`, `docs/superpowers/specs/2026-10-28-self-update-design.md` (append "Plan-time refinements"), `tests/test_docker_files.py` (`.env.example` mentions `SMOS_UPDATE_REPO`)

- [ ] **Step 1: Write failing test** `test_env_example_documents_update_settings` (`SMOS_UPDATE_REPO`, `SMOS_UPDATE_CHECK_MINUTES` present, commented).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Write the documents:
  - `RELEASING.md`: bump `smos/__init__.py` and `pyproject.toml` together; suite green; if any rebuild-list file changed say so in the notes (operators must rebuild; the dashboard will also refuse the update); create GitHub Release `vX.Y.Z` on that commit, not a pre-release; never move a tag; allowed file types in `smos/` (`ALLOWED_SUFFIXES`) and the launcher-is-frozen rule; **note that `.github/workflows` publishes to PyPI on any `v*` tag, decide before the first release whether that is wanted**.
  - `INSTALL.md` "Updating": what the button does, maintenance window (writes get 503 for the restart), rollback behaviour and its data-loss window, rebuild case, CA bundle also used at run time, optional proxy variables, trust statement (GitHub account of the repo owner), disabling with `SMOS_UPDATE_REPO=`, disk headroom 120 %.
  - Spec appendix lists: launcher.py joins the rebuild list; versions are strict `X.Y.Z` tuples (no PEP 440); unpack suffix allowlist; operator-only `SMOS_UPDATE_API_BASE` / `SMOS_UPDATE_ARCHIVE_BASE` for tests; audit rows come from `last-result.json` (a rollback restores the old `admin.db`, so rows are written after restart).
- [ ] **Step 4:** Run `rtk proxy python -m pytest tests/ -q` → all pass (baseline 527 plus new tests, no failures).
- [ ] **Step 5:** Commit `docs(update): releasing checklist, install guide section, spec refinements`.

### Task 12: Real-container verification, handoff, review package

**Files:**
- Create: `docs/update-verification.md`, `docs/superpowers/review/2026-10-28-4-review-package.md` (same format as the three earlier packages)
- Modify: `docs/HANDOFF.md`, new `docs/handoffs/<date>-<time>-project-4-done.md` via the `handoff` skill at session end

- [ ] **Step 1:** Build the image (`docker compose build`, needs Docker Desktop started; stop it afterwards). Serve a local fake GitHub (reuse `tests/fake_github.py`) on the host with two scripted releases built from this working tree: `v9.0.0` (compatible) and `v9.0.1` (its `smos/api/app.py` raises on import, then a second variant that imports fine but never becomes healthy). Run the container with `SMOS_UPDATE_API_BASE`/`SMOS_UPDATE_ARCHIVE_BASE` pointing at `http://host.docker.internal:<port>`.
- [ ] **Step 2:** Verify and record command output for: banner shows 9.0.0 available; confirm page; apply → writes 503 during the window → healthy on 9.0.0 with data intact (ingest a document before, query after); `docker compose down && up` keeps the overlay; a release changing `Dockerfile` shows "needs rebuild"; the import-failing release is refused at the smoke check; the never-healthy release rolls back and the old data and version are back, with the reason on the dashboard and one audit row; killing the container during the swap and starting it again ends consistent.
- [ ] **Step 3:** Write `docs/update-verification.md` (evidence, not claims), the review package, update `docs/HANDOFF.md`; stop the container, the fake server and Docker Desktop.
- [ ] **Step 4:** Run the full suite once more and show the pass count.
- [ ] **Step 5:** Commit `docs(update): verification record, review package, handoff`. Do not push and do not touch Debian (Governor decides).

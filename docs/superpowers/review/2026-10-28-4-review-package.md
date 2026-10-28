# Review package 4: self-update from GitHub Releases (Project 4)

This is the fourth review of the same project. Earlier reviews: `e903ff7..82c1a5c`, `82c1a5c..77165cc` and
`77165cc..75099fe` (`docs/superpowers/review/*-review.md`). Note that the fixes you proposed in the third review
(`dcf38cd`, `4271f65`) were never re-reviewed; they are outside this range, so glance at them only if they touch
what you read here. This package covers everything after the handoff commit: `git diff ede8ab5..HEAD`
(design, plan, implementation). Find real defects (security, data loss, wrong results, gaps against the design).
Style nits are not wanted. Report only; do not edit code.

## 1. Scope

- Repo: this repository, branch `main` (local; nothing has been pushed since `2e8994c`).
- Range: `ede8ab5..HEAD`. Code commits are `cd98c92..8412f2e` (12 commits, listed by part below). Docs: the
  design `docs/superpowers/specs/2026-10-28-self-update-design.md` (binding; read the final section "Plan-time
  refinements", which records deliberate deviations), the plan `docs/superpowers/plans/2026-10-28-self-update.md`,
  `docs/RELEASING.md`, the "Updating from the dashboard" section of `docs/INSTALL.md`, and the verification record
  `docs/update-verification.md`.
- New/changed production code, about +1,530 lines outside tests:
  `smos/launcher.py` (supervisor, new), `smos/update/{config,versions,manifest,github,unpack,status,apply,audit,service}.py`
  (new), `smos/api/app.py`, `smos/api/deps.py`, `smos/admin/{db,pages,dashboard_view}.py`, templates `update.html` and
  `dashboard.html`, `Dockerfile`, `pyproject.toml` (entry point), `.gitattributes`, `.env.example`.
- The PyPI publish workflow was deleted on the Governor's instruction (SMOS is not published to PyPI).
- Commits by part:
  - **A, supervisor:** `af20693` state files and snapshot, `bf1ded6` supervisor with apply, health check, rollback.
  - **B, release logic:** `5b12661` config and versions, `1b4e990` build manifest and Dockerfile step,
    `d15fe15` GitHub client, `3df4a9b` archive extraction and smoke check, `1989a72` status and checker.
  - **C, integration:** `9b2334c` apply orchestration, maintenance mode, audit log, `b508cf8` admin routes and
    dashboard, `8412f2e` lazy import of the update result, `cd98c92` version plumbing, `7215d66` docs.

## 2. Run it

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q        # expect 733 passed, about 80 s
# if a bare `pytest` says "No tests collected" (local hook quirk): rtk proxy python -m pytest tests/ -q
```

Use `python`, not `python3`. The suite is hermetic and offline (a local fake GitHub in `tests/fake_github.py`).
A real run without Docker, driving the real supervisor and a real uvicorn child against that fake GitHub, is
recorded in `docs/update-verification.md`; its script is reproduced there. **A run inside the Docker image has
not been done** (see section 5).

## 3. How it works, in one paragraph

`smos-server` is now `smos.launcher:main`, a stdlib-only supervisor (PID 1 in the container) that runs
`python -m smos.api.main` as a child, with an optional code overlay from `/data/updates/overlay/<version>` first on
`PYTHONPATH`, and restarts the child when it exits with code 75. In the app, `UpdateChecker` asks GitHub for the
latest release (`/releases/latest`), compares its `vX.Y.Z` tag with `smos.__version__`, resolves the tag to a commit
SHA, and compares the git object ids of `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/` and
`smos/launcher.py` at that commit with a manifest the image build wrote (`/opt/smos/build-info.json`); any
difference means "needs rebuild". An admin applies an update from `/admin/update` (CSRF, confirmation): `Updater.begin`
re-evaluates, checks free disk, downloads the archive **by commit SHA**, extracts only `smos/**` with our own writer,
checks `__version__ == tag`, import-tests the staged package in a throwaway interpreter, writes `pending.json`, sets
maintenance mode (writes get 503), waits for in-flight requests, closes the stores and `os._exit(75)`. The
supervisor then snapshots the data dir, moves staging to the overlay, switches `current.json`, starts the child and
waits up to 120 s for `/api/v1/health` to report `status ok` **and the target version**; on failure it kills the
child, restores the data dir from the snapshot and the previous code pointer, and starts the old code. Results go
to `last-result.json`; the app copies them into the admin table `update_log` (migration 4) when it next renders
update state.

## 4. What to attack, hardest first

1. **The archive path is remote code execution; can it be steered?** `smos/update/unpack.py::_extract`,
   `smos/update/github.py` (URL construction, redirect handling, `download_archive`), `smos/update/apply.py::begin`.
   Path traversal, absolute/backslash/drive names, links and devices, a second top-level directory, `smos/` as a
   file, case-insensitive collisions (`smos/a.py` and `smos/A.py` on a case-insensitive volume), Windows reserved
   names, decompression bombs (compressed vs. unpacked size; caps are `max_archive_bytes`, `max_total_bytes`,
   `max_files`, `max_file_bytes`), the order of checks (is anything written before a check that could still
   reject?), and the redirect rule (`_SafeRedirect`: https only, or same-origin loopback). Is there any way a
   request parameter, a release note, a tag or an API response field reaches a filesystem path or a URL other
   than through the validated 40-hex SHA and the configured `owner/name`?
2. **Rollback correctness and data loss.** `smos/launcher.py::Supervisor._apply_pending`, `snapshot_data`,
   `restore_data`, `active_overlay`. Walk the failure matrix: crash at each step (before snapshot, mid-snapshot,
   after snapshot, after switching `current.json`, after the new child migrated the data, during rollback restore);
   a restart with `pending.json` present; a snapshot taken twice; `current.json` / `.prev` handling when there was no
   previous overlay or the previous equals the target; overlay pruning; a partially written snapshot being trusted
   (`.complete` is written last). Does a failed or interrupted update ever end with new code on old data or old
   code on migrated data? We deliberately delete `pending.json` when even the rollback fails (otherwise a restart
   loop); judge that call. Are the WAL/SHM sidecar files of SQLite and the FAISS index handled consistently by the
   copy and the restore?
3. **Maintenance mode and the exit.** `smos/api/app.py` (`maintenance_gate`, `inflight`), `smos/api/deps.py::require_write`,
   `Updater._exit_when_drained`. Is every state-changing route covered (API writes via `require_write`; admin and
   claim non-GET via the middleware)? What changes the database that is neither (the usage-log middleware, the
   sampler, token `last_used_at`, background pruning)? Can a request slip in between the drain check and `close()`,
   or can `os._exit(75)` leave a half-written store? Is `inflight` ever wrong (exceptions, streaming responses)?
4. **Version and "needs rebuild" logic.** `smos/update/versions.py`, `status.py::evaluate`, `manifest.py`
   (git blob/tree id computation, CRLF normalisation of single text files only, directory sort rule, empty
   directories, mode fixed at 100644), `github.py::target_manifest`. Can a release that should be refused be
   offered (or the reverse)? Downgrade protection; the semantics of tag vs. `__version__` vs. commit; tags
   that move between check and apply (we re-resolve the SHA at apply time); GitHub's `releases/latest` ordering
   quirks (it returns the most recent non-draft, non-prerelease release by creation date, not the highest version).
5. **Authorization and CSRF** of the new admin routes (`smos/admin/pages.py`): `admin_required` on all four, CSRF on
   both POSTs, API tokens must not work. Information exposed to an unauthenticated user (the health payload now
   reports the loaded code's version, as before). Release notes are attacker-influenced text rendered on the page
   (`update.html`: escaped by Jinja autoescape, inside `<pre>`); the inline script uses `|tojson`.
6. **Concurrency** in `Updater` (busy lock, state, cooldown), `UpdateChecker` (two locks, rate limit), the exit
   thread versus requests; `service.describe` importing `last-result.json` under `admin_lock` on every dashboard view
   (cost, error handling, idempotence on `update_log.at`).
7. **Failure behaviour when GitHub or the network is bad.** No releases yet (404), rate limit (403/429 with
   `X-RateLimit-Remaining: 0`), HTML error pages, truncated or oversized JSON, DNS failure, TLS inspection with an
   extra CA bundle (`build_ssl_context` adds anchors only), proxy variables. The dashboard must stay a 200.
8. **Packaging.** `Dockerfile` (new `COPY` and `RUN python -m smos.update.manifest` before `USER smos`), the entry point
   change, `.dockerignore` interaction with the `models/` tree id (a file ignored from the context would make the
   in-image tree id differ from git's, i.e. a permanent false "needs rebuild"), the launcher as PID 1 (signal
   forwarding, zombie children, `stop_grace_period: 60s`, the healthcheck still hitting the app), log relay of the
   child's output. A migration (admin DB v4) and the existing migration tests.
9. **Tests that give false comfort.** For each area above, is there a test that would actually fail if the code were
   wrong? Pay attention to `tests/test_launcher_supervisor.py` (fake child and clock) and `tests/test_update_apply.py`.

## 5. Known limits and honest gaps

- Not verified: a run **inside the Docker image** (Docker Desktop is stopped on the dev PC; the image build downloads about
  3 GB). The in-image manifest equals git's object ids only if the build context matches the repository; one unit test
  (`test_repo_rebuild_files_match_git_head`) checks the working tree against `HEAD`, but not the image.
- The supervisor's SIGTERM path is covered by a unit test with fakes (`test_request_stop_terminates_child_and_run_returns`);
  the real signal path needs Linux. On the Windows dev PC the end-to-end script stopped servers by killing the process tree.
- Not verified: long-running behaviour, a very large store (the snapshot is a full copy of the data directory), and the
  dashboard on Debian. Debian has deliberately not been updated.
- Accepted: trust root is GitHub TLS plus the repo owner's account (no signatures); writes between snapshot and a failed
  start would be lost but writes are refused during the window; one shared SQLite connection behind a lock; a release
  that adds a file type outside `ALLOWED_SUFFIXES` under `smos/` is refused until the image is rebuilt.
- Spec deviations (all listed in the design's last section): `smos/launcher.py` is part of the rebuild list; strict
  `X.Y.Z` tags instead of PEP 440; CRLF normalisation in the manifest; operator-only test hooks
  `SMOS_UPDATE_API_BASE` / `SMOS_UPDATE_ARCHIVE_BASE`; `pending.json` deleted when the rollback itself fails.
- A defect found during our own end-to-end planning and fixed in `8412f2e`: the supervisor writes `last-result.json`
  after the new app is healthy, so an import at app start missed it; it is now imported whenever update state is rendered.

## 6. What a good report looks like

Number the findings; for each: file and line, what is wrong, a concrete input or sequence that triggers it, severity
(Critical = data loss, remote code execution or auth bypass; Important = wrong behaviour users will hit; Minor), and
whether you reproduced it. Say what you checked and found sound, so we know the coverage.

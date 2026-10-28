# Self-update (Project 4): verification record

Date: 2026-10-28. Design `docs/superpowers/specs/2026-10-28-self-update-design.md`, plan
`docs/superpowers/plans/2026-10-28-self-update.md`.

## 1. Unit and integration tests

`python -m pytest tests/ -q` → **733 passed** (baseline before this project: 527). New files: `test_versioning`,
`test_launcher_state`, `test_launcher_supervisor`, `test_update_config`, `test_update_manifest`, `test_update_github`,
`test_update_unpack`, `test_update_status`, `test_update_apply`, `test_update_audit`, `test_update_pages`, plus additions to
`test_docker_files`. All offline; GitHub is a local fake (`tests/fake_github.py`).

## 2. Real end-to-end run (no Docker)

Script: `docs/runbooks/scripts/e2e_update.py`. It starts the **real supervisor** (`python -m smos.launcher`) which starts a
**real uvicorn child**, with a local fake GitHub serving releases built from this working tree (version rewritten to
9.0.x) and a build-info file made by the real manifest code. It drives the admin pages over HTTP and reads the files
the supervisor writes. Result: **33/33 checks passed**.

| # | Scenario | Evidence |
|---|---|---|
| 1 | Baseline 0.2.0 up; document ingested and found; repository **without any release** | dashboard 200, "SMOS is up to date", no error |
| 2 | Good release 9.0.0 | banner offers it; confirmation page shows tag, commit and HTML-escaped notes; apply → 303; **writes got 503 + `Retry-After: 30`** during the window; supervisor process survived; health reported 9.0.0; admin session cookie still valid; document still found; `current.json` = 9.0.0 + commit; `last-result.json` ok; dashboard shows "applied"; audit row `applied/admin/0.2.0→9.0.0`; snapshot of the old data exists |
| 3 | Release whose code has a syntax error | offered (the problem is invisible to GitHub), **refused at the import smoke check with 409**; server still 9.0.0; no `pending.json`; staging removed; writes work (maintenance mode not left on); audit row `failed` |
| 4 | Supervisor restarted | the overlay still runs 9.0.0 |
| 5 | Release that **corrupts `metadata.db` and then crashes on start** | apply accepted; child exited before healthy; **rollback**: `last-result` ok=false with the child's output ("simulated broken release"); 9.0.0 serving again; **the corruption is gone** (data restored); the document is still found; `current.json` back on 9.0.0, failed overlay deleted, no `pending.json`; dashboard shows "rolled back"; audit row `rolled_back/admin` |
| 6 | Release that changes the `Dockerfile` | banner "needs a rebuilt image" naming `Dockerfile`; apply refused (409); nothing downloaded |

### What the first run found

- The first attempt had 4 failures that were a **harness problem**, not a product problem: on Windows `Popen.terminate()`
  kills only the supervisor and leaves its server child alive, so "restart the supervisor" left the old server serving
  and the 5-minute cooldown from scenario 3 was still in memory. The script now kills the process tree. (In the
  container SIGTERM reaches the supervisor, which stops its child; that path is unit-tested only.)
- Reasoning about the audit rows while writing this run exposed a **real defect**: the supervisor writes
  `last-result.json` after the new app is already healthy, so the import at app start never saw it. Fixed in `8412f2e`
  (`describe()` imports it whenever update state is rendered), test
  `test_describe_imports_a_result_written_after_startup` RED→GREEN.

## 3. Not verified

- **A run inside the Docker image.** Docker Desktop was stopped and an image build downloads about 3 GB. To do before
  the Debian update: `docker compose build`, then confirm the in-image manifest equals git's object ids, e.g.
  `docker compose run --rm smos cat /opt/smos/build-info.json` against `git rev-parse HEAD:Dockerfile` etc. (CRLF
  normalisation covers the Windows build context for single files; `models/` is hashed raw and relies on
  `.dockerignore` not excluding anything inside it), then run `e2e_update.py`'s scenarios against the container with
  `SMOS_UPDATE_API_BASE`/`SMOS_UPDATE_ARCHIVE_BASE` pointing at a fake GitHub on the host.
- The real SIGTERM path, PID-1 behaviour and zombie reaping in the container.
- Long-running behaviour, a very large store (the snapshot is a full copy), the GitHub rate limit in practice, and the
  update on Debian (deliberately not updated).
- Not yet re-reviewed by Codex: the review fixes of the third review (`dcf38cd`, `4271f65`); this project's own review
  package is `docs/superpowers/review/2026-10-28-4-review-package.md`.

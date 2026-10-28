# Handoff — SMOS standalone: Project 4 (self-update) implemented, Codex review next — 2026-10-28 1700

## State

Projects 1-4 of the five are implemented on `main` (not pushed; 28 commits ahead of `origin/main`, last push `2e8994c`).
Project 4: the dashboard checks **GitHub Releases** (tags `vX.Y.Z`, not commits) and an admin can update the running
container in place; a stdlib-only supervisor (`smos/launcher.py`, now the `smos-server` entry point) snapshots the data,
switches a code overlay in `/data/updates/`, health-checks it **including the reported version**, and rolls back code and
data on failure. 733 tests pass (offline). A **real end-to-end run** (real supervisor + real server + fake GitHub) passed
33/33 (`docs/update-verification.md`, script `docs/runbooks/scripts/e2e_update.py`). **Not done: a run inside the Docker
image** (Docker Desktop is stopped; the image build is about 3 GB), and the Debian installation has NOT been updated and must
not be until the Governor says all updates are finished. Nothing is running: no server, container or watcher.

## Completed this session

- Brainstormed and approved the design (changed from commit-based to release-based at the Governor's request):
  `docs/superpowers/specs/2026-10-28-self-update-design.md` (see its last section for plan-time refinements).
- Plan `docs/superpowers/plans/2026-10-28-self-update.md`, executed inline and test-first, commits `cd98c92..8412f2e`.
- Found and fixed with a test during verification: the supervisor writes `last-result.json` after the app is healthy, so the
  audit import at app start missed it (now imported whenever update state is rendered).
- `docs/RELEASING.md` (release checklist), "Updating from the dashboard" in `docs/INSTALL.md`, `.env.example` settings.
- Removed `.github/workflows/publish.yml` (Governor: SMOS will not be on PyPI). **The PyPI project itself is the Governor's to
  unpublish.**
- Wrote the Codex review package: `docs/superpowers/review/2026-10-28-4-review-package.md`.

## Next steps (in order)

1. Governor gives `docs/superpowers/review/2026-10-28-4-review-package.md` to Codex; reproduce each finding, fix it with a
   test first, record the result (as for reviews 1-3). Optionally include the earlier unreviewed fixes `dcf38cd`, `4271f65`.
2. Before the Debian update: build the image and verify inside it (see `docs/update-verification.md` §3): the in-image
   `/opt/smos/build-info.json` manifest must equal git's object ids for `pyproject.toml`, `Dockerfile`, `compose.yaml`,
   `models`, `smos/launcher.py` (otherwise every release shows a false "needs a rebuilt image"), SIGTERM/PID-1 behaviour, and
   the update scenarios against the container (hooks `SMOS_UPDATE_API_BASE` / `SMOS_UPDATE_ARCHIVE_BASE` to a fake GitHub on the host).
3. Project 5: documentation (user guide, parser-author guide, agent-oriented API guide; README and API.md are short now).
4. Only after all updates are done and the Governor says so: push `main`, then on Debian `git pull`, **rebuild once** (the
   first image with the updater), and re-check (health, login, invitation flow, ingest and query, dashboard, restart
   persistence, then publish a first release `v0.2.1`/`v0.3.0` to try the in-place update; set `__version__` and
   `pyproject.toml` together, see `docs/RELEASING.md`). No release exists on GitHub yet, so the banner says "up to date".

## Open items / blockers

- Push decision: 28 commits unpushed; wait for the Governor.
- A fourth/fifth Codex round is the Governor's call; the Project 4 package is ready.
- Governor-side checks pending on Debian from earlier: `docker compose down && up` persistence and a read-only token being
  refused on upload.
- Dev PC housekeeping the Governor may want: `claude mcp remove smos --scope user`, `pip uninstall smos-mcp`,
  `docker rmi smos:0.2.0` and the pulled base images.
- Known, accepted: Swagger UI loads from a CDN; GraphStore creates its tables outside the migration runner; one shared SQLite
  connection per database; trust root of updates is GitHub TLS plus the repo owner's account (no signatures).
- A `python -m pytest tests/ -q` process that is not ours was running on the dev PC at the end of the session (started
  16:50); it was left alone.

## Key decisions & context

- Updates follow **releases**; running version = `smos.__version__`; only a strictly greater `vX.Y.Z` is offered; archive is
  downloaded by the resolved commit SHA; only `smos/**` with allowed file types is unpacked; "needs rebuild" = any difference in
  the git object ids of `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models`, `smos/launcher.py` (CRLF normalised for the
  single text files).
- Deliberate rulings: rollback failure deletes `pending.json` (avoid a restart loop); writes get 503 from the click until the
  restart (admin/claim non-GET via middleware, `/api/v1` via `require_write`); one snapshot kept, two overlays.
- Execution style unchanged: brainstorm, written design approved, plan, inline TDD, Codex review package; no reviewer subagents.
- No company, CA or host names in the repo (memory note `feedback-no-company-data-in-repo`).
- Windows dev PC: plain `pytest` may say "No tests collected" → `rtk proxy python -m pytest`; heredocs with nested quoting in
  the bash tool can fail, write helper scripts to the scratchpad instead.

## Files that matter

- `smos/launcher.py` (supervisor, frozen between image rebuilds), `smos/update/` (`config`, `versions`, `manifest`, `github`,
  `unpack`, `status`, `apply`, `audit`, `service`), `smos/api/app.py` (maintenance gate, attach), `smos/admin/pages.py`
  (routes `/admin/update*`), `smos/admin/templates/{update,dashboard}.html`, `Dockerfile` (build-info step).
- `docs/superpowers/specs/2026-10-28-self-update-design.md`, `.../plans/2026-10-28-self-update.md`, `docs/RELEASING.md`,
  `docs/update-verification.md`, `docs/runbooks/scripts/e2e_update.py`, `docs/superpowers/review/2026-10-28-4-review-package.md`.
- `docs/HANDOFF.md` — current state and the five-project table.

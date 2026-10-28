# Handoff — SMOS standalone: Projects 1-3 done, Project 4 (self-update) next — 2026-10-28 1550

## State

SMOS is now a standalone, self-hosted HTTP service (no MCP, no built-in AI). Of the five planned projects,
**1 (core API, tokens, migrations), 2 (Docker packaging) and 3 (admin dashboard) are implemented, tested
(527 tests pass, hermetic and offline) and independently reviewed by Codex three times; every finding was
reproduced and fixed.** Project 2 is installed and working on the Governor's Debian server with the code of
Projects 1-2 (verified by ingest and query from a client PC). **The Debian installation has NOT been updated
with the dashboard or later work, and must not be until the Governor says all updates are finished.**
`main` is 11 commits ahead of `origin/main` (last push `2e8994c`); nothing since has been pushed.
No server, container or watcher is running: the dev servers were stopped, Docker Desktop is stopped, the
throwaway `.env` was deleted. Not verified: long-running behaviour, a very large store, the dashboard on Debian,
and the last review fixes (`4271f65`, `dcf38cd`) have not been re-reviewed by Codex.

## Completed this session

- Project 1 core (`e711b4f..82c1a5c`): AI removed, caller-supplied summaries, FastAPI API, token auth via one-time
  invitation links, usage log, migrations, admin pages. Codex reviews 1 and 2 (`a26cb5c`, `8e66924`).
- Token scopes read-only / read-write (`77165cc`).
- Project 2 Docker (`5bc81d9..2e8994c`): Dockerfile, compose, bundled embedding model, install guide, extra CA bundle
  support, `SMOS_HOST_PORT`; installed on Debian (`docs/docker-verification.md`).
- Project 3 dashboard (`8cc641b..75099fe`) and Codex review 3 fixes (`dcf38cd`, `4271f65`):
  `docs/dashboard-verification.md`.
- Three Codex review packages and reports in `docs/superpowers/review/`.

## Next steps (in order)

1. Read `docs/HANDOFF.md`, `docs/runbooks/debian-deploy.md` and `docs/runbooks/environment.md`; run
   `python -m pytest tests/ -q` (expect 527 passed, about 45 s; use `rtk proxy pytest` if "No tests collected").
2. **Start Project 4 (self-update) with the brainstorming skill.** The Governor agreed to the direction: a
   one-click update from the dashboard, with a rebuild only when a release needs one. Decisions to settle and write
   into `docs/superpowers/specs/<date>-self-update-design.md`:
   - Mechanism (recommended): the app downloads the new Python code from the remote `main` into the data volume,
     backs up `metadata.db`/`admin.db`/index, runs migrations, restarts from the overlay, checks health, and rolls
     back automatically on failure. No Docker socket, no privileged mode. Alternative: a host-side helper script.
   - What needs a real rebuild instead (new dependency, new base image, Dockerfile change) and how the dashboard
     says so; version source (compare the running commit with the remote `main` head; tags or releases?).
   - **Security (the update path is remote code execution):** only the configured repo and ref, pin and verify the
     commit SHA, admin-only action with CSRF and a confirmation, audit log entry, never an arbitrary URL, size and
     file-type limits on what is unpacked.
   - Outbound HTTPS from inside the container to GitHub needs the network's CA bundle at run time too (currently only
     used at build time; see `docs/runbooks/debian-deploy.md`) and respects GitHub rate limits and an optional proxy.
   - The dashboard already has the banner slot (`smos/admin/templates/dashboard.html`, "update checking arrives with
     the updater"); the updater adds the version check and the button there.
3. Build Project 4 test-first with the executing-plans skill (inline, no subagents), then write a Codex review package
   (`docs/superpowers/review/<date>-<n>-review-package.md`, same format as the earlier ones).
4. Project 5: documentation (full user guide, parser-author guide, an agent-oriented API guide; README and API.md are
   deliberately short now).
5. Only after all updates are done, and only when the Governor says so: push `main`, then on Debian `git pull`,
   rebuild and re-check (health, login, invitation flow, ingest and query, dashboard, restart persistence).

## Open items / blockers

- Push decision: 11 commits unpushed. Pushing does not change the Debian installation until they pull, but wait for the
  Governor.
- Optional: a fourth Codex package for the latest review fixes; ask the Governor.
- Governor-side checks still pending on Debian (from the Project 2 hand-over): `docker compose down && up` persistence
  and a read-only token being refused on upload.
- Dev PC housekeeping the Governor may want: `claude mcp remove smos --scope user`, `pip uninstall smos-mcp`,
  `docker rmi smos:0.2.0` (and the pulled `python:3.12-slim`, `debian:stable-slim`) to free disk.
- Known, accepted: Swagger UI (`/api/v1/docs`) loads from a CDN (blank offline; the OpenAPI JSON works); GraphStore
  creates its tables outside the migration runner; one shared SQLite connection per database behind locks.

## Key decisions & context

- No MCP, no built-in generative AI; SMOS only stores, retrieves and gathers evidence; parsers/agents supply summaries
  and do the judging.
- Plain HTTP on the internal network; tokens only through one-time invitation links (admin never sees a token); tokens
  are read-only or read/write; develop on `main`.
- The embedding model is bundled in `models/` (Hugging Face is blocked on the target network) and loaded by path; the
  tests use it too. PyTorch GPU wheel from PyPI (CPU index blocked), so the image is large.
- **Never put the employer's name, CA/firewall names, host names or addresses in the repo, commits or remote** (memory
  note `feedback-no-company-data-in-repo`); write such features generically.
- Execution style: brainstorm, written design approved, plan, inline TDD with an evidence line per step, then a Codex
  review package; no reviewer subagents unless asked.
- Deploy/env facts are in `docs/runbooks/debian-deploy.md` (network quirks: HTTPS inspection, blocked sites, port 8765
  vs 8080, browsers forcing HTTPS) and `docs/runbooks/environment.md` (Windows dev PC, Docker Desktop, shell quirks).

## Files that matter

- `docs/HANDOFF.md` — current state and the five-project table (keep it current).
- `docs/superpowers/specs/2026-10-28-*.md`, `docs/superpowers/plans/2026-10-28-*.md` — designs and plans of Projects 1-3.
- `docs/INSTALL.md` — the Debian install guide; `docs/runbooks/debian-deploy.md` — durable environment facts.
- `smos/api/app.py`, `smos/api/routes.py`, `smos/api/deps.py` — API app, middleware, scope enforcement.
- `smos/admin/` — tokens, invitations, usage, pages, dashboard (`dashboard_data.py`, `dashboard_view.py`, `charts.py`,
  `metrics.py`), migrations in `db.py`; `smos/storage/migrations.py` — the migration runner (Project 4 will use it).
- `Dockerfile`, `compose.yaml`, `.env.example`, `certs/README.md`, `models/` — packaging.
- `docs/superpowers/review/` — Codex review packages and reports.

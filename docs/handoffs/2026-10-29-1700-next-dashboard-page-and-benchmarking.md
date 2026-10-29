# Handoff — next session: a new dashboard page and automated benchmarking — 2026-10-29 1700

## State

Everything is committed and pushed (`origin/main` at `856b937` plus this handoff). 779 tests pass. Projects 1-5 are implemented and
Codex-reviewed (review 6, the documentation review, is resolved in `docs/guide-verification.md` section 6). The real repository's latest
release is `v0.3.1`. **Debian runs 0.3.2**: the update drill's overlay (the drill repository's `v0.3.2`) on the 0.3.1 image; the drill
repository on GitHub is deleted, the local clone `C:\Private\smos-update-drill` still exists (the Governor deletes it later).
**The next real release must be 0.3.3 or higher**, never a different `v0.3.2`, or Debian would report "up to date".
No assistant-side server or process is running.

## What the Governor wants next session

1. **A new dashboard page** in the admin area.
2. **Automated benchmarking for the tool** (SMOS), i.e. repeatable benchmark runs instead of the manual one-off scripts we have.

Neither has a design yet. The Governor wants to do it the usual way: brainstorm (start with questions: which page, for whom, what it
shows, what "benchmarking" must answer), written design, plan, inline TDD, Codex package. Do not assume the answers below; they are
starting facts, not decisions.

## Starting facts for the brainstorm

**Dashboard (what exists)**
- Page `/admin/dashboard` (`smos/admin/pages.py:201`, view `smos/admin/dashboard_view.py::build_dashboard`, template
  `smos/admin/templates/dashboard.html`): tiles (documents, segments, database size, API calls, error rate, average latency), charts
  drawn by SMOS itself (`smos/admin/charts.py`, no external assets), update banner, ranges 1h/24h/7d/30d via `?range=`.
- Data layer `smos/admin/dashboard_data.py` (usage-log aggregates, `samples` table of hardware and store measurements every 60 s,
  growth, top calls, per-token activity, recent events) and `smos/admin/metrics.py` (the sampler). Other admin pages: access
  (`/admin/tokens`, invitations), usage (`/admin/usage`), update (`/admin/update`). Admin schema changes need a NEW numbered migration in
  `smos/admin/db.py` (never edit an existing one); the admin DB is at version 4.
- Auth rules: every admin page uses `admin_required`; CSRF on POSTs; every `/api/v1` route except health needs `require_token`
  (`tests/test_api_auth.py` enforces it) and write routes are classified in `tests/test_api_scopes.py`.
- Any change under `smos/` is shipped by a release (0.3.3+); a change to `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/` or
  `smos/launcher.py` needs "a rebuilt image" (see `docs/RELEASING.md`).

**Benchmarking (what exists)**
- Standalone, hand-run scripts, not collected by pytest: `tests/bench_*.py` (retrieval, legal regulations, graph retrieval, phases)
  and `tests/scale_s1/s2/s3_*.py` (corpus, retrieval quality at scale, lifecycle). Some predate the standalone work and import modules
  that moved; check they still run before reusing them.
- Recorded results: `docs/eu-law-benchmark-results.md`, `docs/toolchain-benchmark-results.md` (an older MCP-era comparison); a fair
  comparison needs the same mechanism on both sides (memory `eu-law-benchmark-result`).
- `docs/hnsw-assessment.md` has a latency probe (flat search 1.4 / 4.5 / 12.8 ms per query at 10k / 100k / 300k segments on the dev
  PC; random vectors, so no recall figures). The `ARCHITECTURE.md` section 4 latency numbers are older and look pessimistic: a
  benchmark on the Debian host would settle them.
- The example parser and a real server harness exist: `docs/examples/standards_pdf_parser.py`, `tests/helpers.py::make_pdf`, and the
  probe pattern of starting `python -m smos.api.main` on a temp data dir (see `docs/guide-verification.md` section 1).
- Open questions for the Governor (ask, do not decide): what the benchmark must answer (speed, retrieval quality, ingest throughput,
  regression detection between releases); where it runs (dev PC, Debian, inside CI-less repo); whether results are shown on the new
  dashboard page, stored in `admin.db`/a file, or both; whether it runs from the dashboard (a button) or from the command line;
  which corpus (synthetic, the example standards, EU-law); acceptable run time and load on the production server.

## Technical debt candidates (from Project 5; each is a SMOS change and a release)

See `docs/guide/known-limits-and-roadmap.md` and `docs/handoffs/2026-10-29-1530-project-5-documentation-done.md`: no adapter selected over
the API, `tags` not a real filter on `/query`, accepted-but-ignored contract fields, normative scope hard-wired, short heading blocks
glued to the previous segment, no endpoint to read quarantine or to delete/list documents, a query always returns nearest items.
A benchmark that measures retrieval quality would also show whether fixes to these help.

## Pending on the Governor (unchanged)

- The two Debian checks (steps in `docs/guide-verification.md` section 4): persistence across `docker compose down` / `up -d` (no
  `-v`), and a read-only token refused with 403 on `POST /api/v1/documents`.
- Read the guide as a newcomer. Delete `C:\Private\smos-update-drill`. Unpublish the old PyPI project.
- Optional: `docker restart smos` during the 120 s health wait of a failing update (needs another throwaway repository).

## Working notes that will matter

- Test command: `rtk proxy python -m pytest tests -q` (bare `pytest` reports "No tests collected"); about 100 s; `pypdf` is installed on
  the dev PC so the example tests run (they skip without it).
- Bash python heredocs mangle backslash-n escapes in written files; use the Edit/Write tools for text with backslashes and check with
  `ast.parse` (cost this session about 6 detours).
- Debian: one command at a time, the Governor pastes output; the rebuild recipe and the drill record are in
  `docs/runbooks/debian-deploy.md`. Pushes to repositories other than this one need the Governor's explicit permission (a push to the
  drill repository was blocked by the classifier and done by the Governor by hand).
- Execution style: brainstorm, approved written design, plan, inline TDD, Codex review package; no reviewer subagents; mutation-check
  new guard tests (make them fail on purpose once).
- No company, host or CA names in the repository (memory `feedback-no-company-data-in-repo`).

## Files that matter

`docs/HANDOFF.md`, `docs/guide/known-limits-and-roadmap.md`, `smos/admin/dashboard_data.py`, `smos/admin/dashboard_view.py`,
`smos/admin/metrics.py`, `docs/hnsw-assessment.md`, `tests/scale_s2_retrieval.py`, `docs/runbooks/debian-deploy.md`.

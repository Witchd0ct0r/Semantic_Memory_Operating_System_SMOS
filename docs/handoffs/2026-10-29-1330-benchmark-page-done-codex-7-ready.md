# Handoff — Benchmark page implemented; Codex package 7 ready — 2026-10-29 1330

## State

Project 6, the admin **Benchmark page**, is implemented and committed on `main` (not pushed): 12 commits, `397199f` to `5f529be`
(base `8e1013d`). 924 tests pass. It was verified for real on the dev PC with a throwaway SMOS server: a **Quick** run through the
page (208 s), **Cancel** (idle 3 s later), and a **hard kill** of the service mid-run (runner gone within 1 s; after restart the row
was `interrupted`, `bench-tmp/` empty). In all cases the real store was byte-identical afterwards. **Not run: Standard and Full**
(the Governor chose to run them on the Debian host), so the comparison and verdict thresholds are uncalibrated. No assistant-side
server or process is running (the throwaway server on port 18765 was stopped; checked with netstat and a process scan).

## Completed this session

- Brainstorm, written design, plan and native (inline) TDD execution of all 12 plan tasks (`docs/superpowers/specs/2026-10-29-benchmark-design.md`,
  `docs/superpowers/plans/2026-10-29-benchmark.md`).
- What it does: *Start* on `/admin/benchmark` (POST, CSRF) puts `/api/v1` in maintenance (503, health stays 200), runs a separate
  runner process on generated text in `<data>/bench-tmp/<id>/`, shows progress and Cancel, stores the result (admin DB migration 5,
  newest 50 kept), shows a chart report with an HNSW verdict, comparison with the previous run, Download JSON and **Export PDF**
  (browser print), and deletes everything afterwards (verified cleanup). Started **only** from the admin page (launch secret).
- Found and fixed by the real checks: a hard-killed SMOS left its runner running (now: runner parent watchdog + start-up orphan
  cleanup); the orphan scan first over-matched any command line mentioning the path (killed the assistant's own shell) and now matches
  only Python benchmark processes, never self or ancestors.
- Docs: `docs/benchmark-verification.md` (real run record, numbers, observations, gaps), `docs/INSTALL.md` (benchmark section),
  `ARCHITECTURE.md` section 4 and `docs/hnsw-assessment.md` (measured update), `docs/runbooks/benchmark.md`.
- Codex review package: `docs/superpowers/review/2026-10-29-7-review-package.md`.

## Next steps (in order)

1. **Governor: send `docs/superpowers/review/2026-10-29-7-review-package.md` to Codex** (report goes to
   `docs/superpowers/review/2026-10-29-7-review.md`), then resolve findings the usual way (RED test, fix, record in
   `docs/benchmark-verification.md` as a section 7).
2. **Release 0.3.3 or higher** (a change under `smos/` only, no rebuilt image). Debian runs 0.3.2 (the drill overlay), so the tag must
   not be 0.3.2. Decide with the Governor when (after the Codex review is sensible).
3. **Governor on Debian** (steps in `docs/runbooks/benchmark.md`): run Quick, then Standard twice, download both JSON reports.
4. **Calibrate** with those two Standard reports: set the 10%/20% comparison flags and the verdict thresholds (3x headroom, 25% vector
   share) from the real run-to-run noise; replace the latency figures in `ARCHITECTURE.md` section 4 with Debian numbers.
5. Pick the next project from the debt list (see "Observations" below and `docs/guide/known-limits-and-roadmap.md`).

## Open items / blockers

- Standard/Full not run anywhere; thresholds uncalibrated (see `docs/benchmark-verification.md` section 5).
- Windows-only evidence for process-tree/orphan handling; not run on Debian/Docker.
- Still pending from before: the two Debian checks (`docs/guide-verification.md` section 4); read the guide as a newcomer; delete
  `C:\Private\smos-update-drill`; unpublish the old PyPI project.
- Deferred minors: the page can hang up to 30 s while the start waits for the API drain (lock held); the JSON download would 500 on
  a NaN in a stored result (none produced today); chart y-axis ticks like "6.2 req/s"; PDF is about a page per chart.

## Key decisions & context

- Maintenance mode, not a separate service: the page runs inside the live service; the run is a separate process on a throwaway store
  (decided with the Governor). PDF = print to PDF, no new dependency (Governor chose it over a server-side PDF library).
- Standard runs skipped here at the Governor's choice (about 15 minutes of heavy CPU each on the dev PC).
- Ruled during execution (all in the plan's ledger, summarised in the final message of that session): paragraphs capped at 110 words;
  `list_runs` returns parsed results; `BenchServer` makes its own token; `api_inflight` counter added (the admin Start request itself
  keeps the old counter above zero); route and service both validate the budget; report parts are guarded against junk results.
- **Observations from the first real run** (dev PC, Quick, generated text; numbers in `docs/benchmark-verification.md` section 2):
  throughput peaks near 23 requests/s and does not scale with clients (CPU-bound, the embedding model's threads use 8-9 cores);
  **BM25 grows 1.8 -> 21.5 ms from 516 to 5,157 segments while vector search stays at 2-3 ms**, so the BM25 scan is the first thing to
  fix, not HNSW; needle hit@1 on synthetic text falls from 0.62 to 0.28 with size (worth a look with real identifiers).
- Working style notes that cost time this session: the browser tool cannot open `file://` (serve through the app instead);
  `pdftoppm` is not installed (PDF checked via headless Chrome `--print-to-pdf` and pypdf); never match processes by a path in their
  command line.
- Reusable facts are in `docs/runbooks/benchmark.md` (new) and `docs/runbooks/debian-deploy.md`.

## Files that matter

- `docs/benchmark-verification.md` — what was run, numbers, gaps, the Debian steps.
- `smos/bench/service.py` — start, supervise, cleanup, orphan handling, `safe_run_dir`.
- `smos/bench/runner.py` — measuring process, launch guard, parent watchdog.
- `smos/api/app.py` (`maintenance_gate`, `api_inflight`), `smos/update/apply.py` (refusal) — the touched core paths.
- `smos/admin/benchmark_view.py`, `smos/admin/pages.py`, templates `benchmark.html` / `benchmark_report.html` — page and report.
- `docs/superpowers/review/2026-10-29-7-review-package.md` — what to attack.

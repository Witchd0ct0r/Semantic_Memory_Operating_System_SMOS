# Handoff — Benchmark page shipped to main; Codex 7 resolved; Debian runs pending — 2026-10-29 1359

## State

The admin **Benchmark page** (project 6) is implemented, Codex-reviewed (review 7: 5 Important + 3 Minor, all fixed test-first) and
**pushed to `origin/main`** (`8e1013d..b95f3b4`, 15 commits). 936 tests pass; the tree is clean and in sync with origin. Nothing is
tagged or released: Debian still runs 0.3.2 (the update drill's overlay), so the next release must be **0.3.3 or higher**. Verified for
real on the dev PC before the review fixes: a Quick run through the page, Cancel, and a hard kill of the service mid-run (all clean,
real store untouched). **Not verified:** Standard/Full runs anywhere, anything on the Debian host, a real run *after* the review fixes
(only the suite's smoke run, which starts a real server), the interactive print dialog and non-Chrome browsers. No server or process
started by the assistant is running (checked by process scan; the throwaway server was stopped earlier).

## Completed this session

- Brainstorm, design, plan, native TDD of 12 tasks, real-run verification, Codex package 7 and its resolution:
  `docs/superpowers/specs/2026-10-29-benchmark-design.md`, `docs/superpowers/plans/2026-10-29-benchmark.md`,
  `docs/benchmark-verification.md` (sections 1-7), `docs/superpowers/review/2026-10-29-7-review-package.md`, `...-7-review.md`.
- Behaviour: *Start* on `/admin/benchmark` (admin session + CSRF only; `python -m smos.bench` refuses by hand) puts `/api/v1` in
  maintenance (503, health 200), runs a separate runner on generated text in `<data>/bench-tmp/<id>/`, shows progress and Cancel,
  stores the result (admin DB migration 5, newest 50), report with 11 charts, HNSW verdict, comparison with the previous run,
  Download JSON, Export PDF (browser print), verified cleanup, orphan protection after a hard kill.
- Codex 7 fixes (commit `b95f3b4`, tests `tests/test_bench_review7.py`): junction-safe `safe_run_dir`, path-component orphan matching,
  shared update/benchmark interlock (`app.state.transition_lock`), supervisor-start failure cleanup, final-row retry on a failed result
  write, mixed-load error reporting, memory-figure rename, two-model-copy memory estimate and model release/warm-up.
- Docs: `docs/runbooks/benchmark.md`, `docs/INSTALL.md` (benchmark section), `ARCHITECTURE.md` section 4 and `docs/hnsw-assessment.md`
  (measured update), `docs/HANDOFF.md`.

## Next steps (in order)

1. **Governor: release 0.3.3 or higher** (code under `smos/` only, no rebuilt image needed; follow `docs/RELEASING.md`). Never tag 0.3.2.
2. **Governor on Debian** (steps in `docs/runbooks/benchmark.md`): update from the dashboard, run **Quick**, then **Standard twice**,
   *Download JSON* for each report (and check `bench-tmp` is empty and no `smos.bench` process remains afterwards).
3. **Calibrate with those reports** (next session): from the two Standard runs set the comparison flags (10%/20%) and the verdict
   thresholds (3x headroom, 25% vector share) in `smos/bench/analysis.py` from real run-to-run noise; replace the dev-PC latency
   paragraph in `ARCHITECTURE.md` section 4 with Debian numbers; add the results to `docs/benchmark-verification.md`.
4. **Choose the next project** from the evidence (brainstorm first): the first real run showed **BM25 query time growing 1.8 -> 21.5 ms
   (516 -> 5,157 segments) while vector search stays 2-3 ms** (fix the BM25 full scan before any HNSW work), throughput ~23 req/s and
   CPU-bound (more clients only add latency), and exact-identifier needle queries getting worse with size on synthetic text
   (hit@1 0.62 -> 0.28; check with real identifiers / the `exact` strategy). Other candidates: `docs/guide/known-limits-and-roadmap.md`.

## Open items / blockers

- Standard/Full never run; thresholds uncalibrated (`docs/benchmark-verification.md` section 5). Needs the Debian host.
- Windows-only evidence for process-tree/orphan/watchdog behaviour; Linux/Docker paths untested on the host.
- Still pending from earlier: the two Debian checks (`docs/guide-verification.md` section 4); read the guide as a newcomer; delete
  `C:\Private\smos-update-drill`; unpublish the old PyPI project.
- Deferred minors: the Benchmark page can hang up to 30 s while a start waits for the API drain (lock held); the JSON download would
  500 on a NaN in a stored result (none produced today); chart y-axis ticks like "6.2 req/s"; the PDF is about a page per chart.

## Key decisions & context

- Maintenance mode, not a separate service; run in a separate process on a throwaway store; PDF = browser print (Governor's choices);
  Standard runs skipped on the dev PC at the Governor's request (heavy CPU); start only from the admin panel (launch secret) — all in
  the spec. Execution was native/inline with a self-review (no reviewer subagents, per the Governor's standing rule).
- Lessons worth keeping (also in `docs/runbooks/benchmark.md`): never find or kill processes by a path in their command line (a first
  orphan scan killed the assistant's own shell); a hard-killed SMOS leaves its runner running (now handled); the browser tool cannot
  open `file://` (serve through the app); `pdftoppm` is not installed (PDF checked with headless Chrome `--print-to-pdf` + pypdf);
  multi-heredoc bash commands got mangled by the shell wrapper, so put multi-line Python edits in script files.
- Test command: `rtk proxy python -m pytest tests -q` (about 3 minutes; the runner/service tests start real processes).
- No company, host or CA names in the repository.

## Files that matter

- `docs/benchmark-verification.md` — what was run, numbers, gaps, review resolution.
- `docs/runbooks/benchmark.md` — Debian steps and the dev-PC driving recipe.
- `smos/bench/service.py`, `smos/bench/runner.py` — supervision/cleanup and the measuring process.
- `smos/bench/analysis.py` — verdict and comparison thresholds to calibrate.
- `smos/api/app.py` (`maintenance_gate`, `transition_lock`), `smos/update/apply.py` — the touched core paths.
- `smos/admin/benchmark_view.py`, `smos/admin/pages.py`, templates `benchmark.html` / `benchmark_report.html` — page and report.

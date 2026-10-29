# Review package 7: the Benchmark page (admin), project 6

Seventh review round. Earlier: package 6 (documentation, resolved in `docs/guide-verification.md` section 6). This package covers the
benchmark work: `git diff 8e1013d..HEAD` (design `docs/superpowers/specs/2026-10-29-benchmark-design.md`, plan
`docs/superpowers/plans/2026-10-29-benchmark.md`, verification record `docs/benchmark-verification.md`). **This one changes SMOS
behavior**: a new admin page that can take the whole `/api/v1` surface out of service on purpose, runs a child process, deletes a
directory tree, and touches the app's request middleware, the updater and the admin schema (migration 5). Report only; do not edit.

## 1. Scope

- New package `smos/bench/`: `service.py` (start, supervise, cancel, cleanup, startup recovery, `safe_run_dir`), `runner.py` (the
  measuring process, launch guard, parent watchdog), `phases.py` (in-process phases), `httpload.py` (loopback server and load
  client), `corpus.py` (generated text and probes), `analysis.py` (HNSW verdict, run comparison), `stats.py`, `presets.py`.
- Admin side: `smos/admin/benchmarks.py` (repository), `benchmark_view.py` (report model), `charts.py` (new `xy_chart`,
  `stacked_bar`, `bar_chart`, `data_table`), `pages.py` (6 routes), templates `benchmark.html`, `benchmark_report.html`,
  migration 5 in `db.py`.
- Existing files changed: `smos/api/app.py` (gate, `api_inflight`, service wiring, close chain), `smos/update/apply.py` (refuses
  while a benchmark runs), `smos/admin/templates/base.html` (nav link, chart colours).
- Tests: `tests/test_bench_*.py` (13 files), helpers `tests/benchdata.py`, `tests/fake_bench_runner.py`.
- Docs touched: `ARCHITECTURE.md` section 4, `docs/hnsw-assessment.md`, `docs/INSTALL.md`, `docs/HANDOFF.md`.

## 2. Run it

```bash
rtk proxy python -m pytest tests -q          # expect 924 passed (about 3.5 minutes; the runner and server tests start real processes)
```

There is deliberately no command that starts a benchmark by hand: `python -m smos.bench ...` exits with code 2 unless the admin
page launched it (one-time secret). To see it work end to end start a throwaway server (`SMOS_DATA_DIR` on a temp folder, an admin
account, `SMOS_EMBEDDING_MODEL_DIR=models/all-MiniLM-L6-v2`), log in, open `/admin/benchmark`, press *Start* on **Quick**. The tests
use a hidden `smoke` preset through `smos.bench.runner.run()` (a library function, not an entry point).

## 3. What to attack, hardest first

1. **The maintenance gate** (`smos/api/app.py`, `maintenance_gate`). Any `/api/v1` path that still answers during a run (besides
   `/api/v1/health`); paths that bypass the prefix check (trailing slashes, `//api/v1`, case, percent-encoding, `/api/v1.json`,
   mounted sub-apps); what happens to a request that passed the gate just before the flag was set (`api_inflight` and the 30 s drain
   in `BenchmarkService._drain`); a flag that stays set (every exit route of `_watch`, an exception in `_store`, a crash between
   `benchmark_active = True` and the watcher thread starting); the health check staying 200 so Docker does not restart the container.
2. **Cleanup and deletion safety** (`smos/bench/service.py`). Everything that ends a run (completed, failed, cancelled, runner crash,
   runner killed, wall-time cap, supervisor exception) must kill the process tree, delete the directory, verify it and clear the
   flag. Attack `safe_run_dir` (symlinks, junctions, `bench-tmp` itself a link, case on Windows, trailing dots/spaces, an id that
   is an int subclass, very large ids), `recover_on_startup` (what it deletes and what it must leave alone) and `_kill_orphans`
   (it only matches Python processes running `smos.bench` with an argument under `<data dir>/bench-tmp`, or `smos.api.main` with
   `SMOS_DATA_DIR` there, and never this process or its ancestors; a first version matched any command line that merely
   mentioned the path and killed the assistant's own shell, so look hard for remaining over-matching, a data directory that is a
   prefix of another path, and the kill of a process whose children list is stale).
   Windows and Linux differences in `psutil` tree kills, zombies and `start_new_session`.
3. **Starting only from the admin panel** (spec 7.2). Any way to start a run without an admin session: API, token, GET, a second
   caller of `BenchmarkService.start`, the CLI, the launch secret (`SECRET_ENV`, `launch.sha256`, `check_launch_authorisation`), a
   race on the hash file, a work directory crafted to satisfy `work_dir.parent.name == "bench-tmp"`. The guard is advisory against
   someone with a shell (they can run Python); say if it is weaker than that.
4. **Updater and restart interplay.** `Updater.begin` refuses while `benchmark_active`; `BenchmarkService.start` refuses while an
   update is staging or `maintenance`/`closing` is set. Look for the window between the two checks, a leftover `bench-tmp` inflating
   the updater's snapshot size (`_data_bytes`), and the close chain in `create_app` (`close_all`).
5. **Measurement honesty** (`phases.py`, `httpload.py`, `analysis.py`). `timed_context` mirrors `build_context` by hand (a test compares
   their ids; check what it does not compare); HNSW recall methodology (same vectors, same queries, `efSearch`, k larger than the
   index); percentile and projection maths; the verdict thresholds (3x headroom, 25% vector share) and whether the "not needed"
   wording can mislead (the Quick report said "not needed" while projecting the budget at about 5x the measured size, and BM25, not
   vector search, was the growing part); client-limited detection (`process_time` share over wall time, threads and the GIL);
   "cold" query definition; the writer in `mixed_step`; anything that lets a number look better than it is.
6. **Pages and stored data** (`pages.py`, `benchmark_view.py`, templates). Stored strings are rendered with `|safe` only for SVG
   and tables built by `charts.py` (check every string that reaches them is escaped, including series names, labels, tooltips);
   CSRF on both POSTs; error pages that echo input; junk or future-format stored results (the view guards each part, check what a
   hostile `result_json` could still do, including very large numbers, NaN/inf, deeply nested data, huge lists); the download route
   (`Content-Disposition`); the print stylesheet and `beforeprint` script.
7. **Resources and denial of service by the admin.** Memory and disk checks are estimates (`requirements`); the runner loads a second
   embedding model; a run that fills the disk, the wall-time cap, runaway loops in the sweep, 64 client threads, the usage log not
   being written for the runner's server (its own throwaway admin DB), the sampler continuing during a run (the dashboard shows the
   spike), and prune/retention (`KEEP_RUNS = 50`, a running row is never pruned).
8. **Migration 5** (`db.py`) and anything reading `schema_version`; `tests/test_update_audit.py` had its version pin changed from 4
   to `ADMIN_MIGRATIONS[-1][0]`.

## 4. Known limits and honest gaps

- Only the **Quick** preset ran for real (dev PC); **Standard and Full were not run** (decided with the Governor, to be run on the
  Debian host). Verdict and comparison thresholds are design values, not calibrated (`docs/benchmark-verification.md` section 5).
- Windows-only evidence for process-tree, orphan and watchdog behaviour; the Linux/Docker paths are covered by tests that run on any
  OS but were not run on Debian. A hard kill of the runner itself would orphan its benchmark server (the service kills the tree
  on every ordinary exit and start-up kills orphans, but there is no watchdog in the server child).
- PDF export is the browser's print dialog (decided); the layout was checked through headless Chrome only (12 pages for Quick).
- The test corpus is synthetic; quality figures are labelled as such. The needle-query result (hit@1 falls with size) is an
  observation about the hybrid query path on generated text, not a benchmark defect claim; a reviewer who thinks the probes are
  unfair should say so.
- The load client is `http.client` threads (no new dependency); client-limited detection is the guard for its ceiling.
- Deferred minors: chart y-axis tick labels use the dashboard's rounding (for example 6.2 req/s); the PDF has about one page per
  chart because every chart carries its data table.

## 5. How to report

Per finding: severity (Critical / Important / Minor), the file and line, the scenario (inputs or sequence of events), what goes
wrong, and a one- or two-sentence fix. Write the report to `docs/superpowers/review/2026-10-29-7-review.md`.

# Benchmark page: verification record (2026-10-29)

Design `docs/superpowers/specs/2026-10-29-benchmark-design.md`, plan `docs/superpowers/plans/2026-10-29-benchmark.md`. This record
holds what was run for real (not only unit tests), the evidence for the cleanup and "start only from the admin panel"
requirements, and what is still open.

## 1. What was run

A throwaway SMOS server (`python -m smos.api.main`, port 18765, temporary data directory, bundled model) on the dev PC (12 logical
cores, 31 GB RAM, Windows, FAISS 1.14.3, SMOS 0.3.1 code plus this work). The data directory held 62 segments of "real" data
(generated, but written before the run) so that the checks below could prove the real store is untouched. The server was driven
through the real admin pages over HTTP, with a browser used to look at the result. The server was stopped afterwards.

| Scenario | Result |
|---|---|
| **Quick run** through *Start* | completed in 208 s; see section 2 |
| During the run | `/api/v1/health` 200, `POST /api/v1/documents` 503, `POST /api/v1/query` 503 |
| After the run | health 200 and query 200 again; `bench-tmp/` empty; no runner or benchmark-server process left |
| Real store | `faiss.index` SHA-1 and memory count identical before and after (62 segments) |
| **Cancel** pressed at 10% | the page was idle 3.0 s later; `bench-tmp/` empty; row `cancelled`; store unchanged |
| **Hard kill** of the live server mid-run (`taskkill /F`) | runner and its server were gone within 1 s (the runner notices its parent died); the run directory stayed (nobody to delete it); after restarting SMOS the row was `interrupted`, `bench-tmp/` was empty and the page was idle |

The hard-kill check found a real gap that unit tests had not: a killed SMOS left its runner running (a separate process). Fixed
test-first: the runner exits when its parent is gone (`start_parent_watchdog`), and start-up ends processes whose command line or
data directory lies inside `bench-tmp/` (`BenchmarkService._kill_orphans`).

## 2. Quick run: measured on the dev PC

(One run: single-run numbers, not calibrated. Generated text; 4 sizes up to 5,157 segments.)

| Segments | Ingest seg/s | Query p50 / p95 / p99 ms | Start-up s | Stage ms: embed / vector / BM25 / total | HNSW recall@10 | Needle hit@1 / hit@10 (synthetic) | Server RSS MB |
|---|---|---|---|---|---|---|---|
| 516 | 75 | 24 / 30 / 42 | 8.9 | 11.2 / 2.1 / 1.8 / 16.4 | 1.000 | 0.62 / 0.95 | 502 |
| 1,037 | 69 | 26 / 29 / 29 | 9.4 | 15.5 / 2.7 / 3.7 / 23.5 | 1.000 | 0.52 / 0.90 | 507 |
| 2,582 | 69 | 35 / 38 / 42 | 8.4 | 12.7 / 2.4 / 9.9 / 26.9 | 1.000 | 0.37 / 0.78 | 524 |
| 5,157 | 65 | 51 / 60 / 63 | 8.9 | 12.8 / 2.6 / 21.5 / 39.7 | 1.000 | 0.28 / 0.62 | 547 |

Load at 5,157 segments (one server process, one core of the load client used):

| Clients | Requests/s | p95 ms | Errors | Server CPU (% of one core) |
|---|---|---|---|---|
| 1 | 18.7 | 61 | 0 | 864 |
| 2 | 22.6 | 100 | 0 | 818 |
| 4 | 20.6 | 221 | 0 | 865 |
| 8 | 19.7 | 454 | 0 | 867 |

Mixed load (2 readers, one writer ingesting): read p95 129 ms alone, 274 ms with the writer; reads per second 21.0 against 12.6.
Verdict shown: *HNSW not needed* (p95 60 ms against the 200 ms budget; projection reaches the budget at about 25,700 segments).

### What it shows (observations, for the Governor and the next design)

- **Throughput is CPU-bound and does not scale with clients.** Peak 22.6 requests/s at 2 clients; more clients only add latency.
  The server process uses 8-9 cores' worth of CPU for one request stream (the embedding model's own threads), so there is no spare
  capacity for parallel requests on this machine.
- **BM25, not vector search, is what grows.** Vector search stays at 2-3 ms; BM25 query time goes 1.8 -> 21.5 ms across 516 ->
  5,157 segments and is already 54% of a query. This matches the documented debt (the BM25 index is a full in-memory scan) and is
  the first thing to fix before HNSW matters; HNSW would not help this.
- **HNSW** found 100% of the exact top 10 at these sizes and built in under 0.1 s, so there is no recall argument against it yet,
  and no speed argument for it either. The assessment in `docs/hnsw-assessment.md` stands.
- **Retrieval of an exact identifier gets worse with size** on this synthetic corpus: needle hit@1 falls from 0.62 to 0.28 (hit@10
  0.95 -> 0.62). The queries contain the unique identifier plus a few topical words. The default hybrid query path fuses vector and
  keyword ranks, so an exact identifier does not dominate when many paragraphs share vocabulary. Synthetic text exaggerates this; it is
  worth a look with real requirement identifiers (the `exact` recall strategy is the documented way to fetch by identifier).
- **Start-up** is about 9 s at every size here and is dominated by loading the embedding model, not by the index.

## 3. Print to PDF

The Export PDF button calls `window.print()`. Checked with the saved report printed through Chrome (`--print-to-pdf`, headless):
12 pages; the navigation, buttons and "Show the numbers" toggles are not in the PDF; every chart's data table is expanded (the page
opens them on `beforeprint`); the report header (id, date, version, preset) is on every page. The charts were also looked at with the
print rules applied (screenshot): all 11 charts, the tiles, the verdict box and the comparison table are complete and readable.
Not checked: the interactive print dialog and browsers other than Chrome (the Governor's browsers are not known to the assistant).
In the PDF each chart plus its table takes about a page, so a Quick report is 12 pages.

## 4. Start only from the admin panel

Pinned by tests (`tests/test_bench_pages.py`, `tests/test_bench_runner.py`, `tests/test_bench_service.py`): no OpenAPI route mentions
benchmarks; a valid read/write bearer token on `POST /admin/benchmark/start` is redirected to login and starts nothing; a GET on the
start URL is 405; a scan of `smos/` finds the service's `start` called from exactly one place (the route) and the service created in
exactly one place; `python -m smos.bench` exits 2 ("benchmarks are started from the admin page") without the one-time launch secret,
with a wrong one, with a work directory outside `bench-tmp/`, or without the hash file (checked for real once too: running it by hand
printed the refusal and exited 2). The secret travels in the child's environment only; a test confirms it is not in argv or in logs.
Mutation checks: a second `bench.start` caller, a missing CSRF check, a skipped path check and a gate that lets a path through each
make a test fail.

## 5. Known gaps and what is still open

- **Standard and Full presets were not run** (decided with the Governor: they run on the Debian host). The 10% / 20% comparison
  thresholds and the HNSW verdict thresholds are design values, **not calibrated**: they need at least two Standard runs on the same
  machine to show run-to-run noise. The first Standard run on Debian also replaces the pessimistic latency figures in
  `ARCHITECTURE.md` section 4 with measured ones for that machine.
- Only one real run has a report, so the comparison table was exercised by tests only (a "no earlier run" page was seen for real).
- Windows-only evidence for process-tree and orphan handling; the Linux/Docker behaviour (process groups, `docker stop`) is covered by
  the same code paths and by tests that run on any OS, but has not been run on the Debian host.
- Y-axis tick labels use the dashboard's rounding (for example 6.2 req/s); cosmetic.
- The Export PDF page count is high (about a page per chart because each carries its table).

## 6. For the Governor on Debian

1. Update to the release that contains this work (0.3.3 or higher; a change under `smos/` only, no rebuilt image needed).
2. Open *Benchmark*, run **Quick** first (a few minutes, the API is unavailable meanwhile), confirm the report and that `/api/v1`
   answers again afterwards.
3. Run **Standard** twice. Send the two reports (Download JSON) so the thresholds can be calibrated.
4. Check `ls <data dir>/bench-tmp` is empty afterwards and `docker top smos` shows no leftover `smos.bench` process.

## 7. Codex review 7: resolution

`docs/superpowers/review/2026-10-29-7-review.md`: 5 Important and 3 Minor, all fixed test-first (RED shown, then GREEN; the new
guards were mutation-checked). Tests are in `tests/test_bench_review7.py`; suite 936 passed.

| # | Finding | Fix |
|---|---|---|
| 1 | A Windows junction at `bench-tmp` redirects cleanup outside the data dir | `safe_run_dir` rejects links and reparse points (`_is_link`) for `bench-tmp` and the run directory, and requires `bench-tmp` to resolve directly inside the data directory |
| 2 | Orphan kill matched a prefix-sibling path (`bench-tmp-other`) | `_inside` compares whole path components (normalised, absolute); tests for a sibling server and a sibling runner path |
| 3 | Update and benchmark could both pass their checks | one `app.state.transition_lock`: `Updater.begin` checks the benchmark flag and claims "staging" in one step, `BenchmarkService.start` checks the updater and sets its flag in one step; a test pauses the updater inside its window and proves the benchmark refuses |
| 4 | A supervisor thread that fails to start leaves a live runner and the flag set | `_abandon`: kill the runner family, delete the directory, record `failed`, clear the flag, then refuse |
| 5 | A failed result write leaves a `running` row that blocks every later run | `_store` retries a minimal final `failed` row (up to 3 times) naming the storage error |
| 6 | Mixed-load p95 hid failed reads | `mixed_step` returns read errors and client-limited flags for both phases; the report table shows them and the p95 is labelled "successful reads" |
| 7 | "peak" RSS was a single post-probe sample | renamed `largest_post_probe_rss_bytes` (`runner.RSS_KEY`); older stored reports keep the old key and still render |
| 8 | RAM estimate and install note undercounted model copies | the runner frees its model (`release_model`) before the server starts and the memory estimate is now 2.2 GiB plus 6 KiB per segment; the install note says two copies. Also found while fixing this: the model load was inside the timed ingest, so ingest rates were understated at small sizes; the model is now loaded before timing (`_open_and_warm`) |

Not re-run for real after these fixes (only the runner's smoke run in the suite, which starts a real server); the Quick run on Debian
is the next real check. Quick-run numbers in section 2 predate fixes 7 and 8 (ingest rates there include model loading).

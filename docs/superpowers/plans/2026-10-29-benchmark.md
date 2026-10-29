# Benchmark Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin "Benchmark" page that runs a self-measurement of SMOS on its own hardware (service in maintenance, throwaway store), shows a chart report with an HNSW verdict, exports it as PDF by printing, and cleans up after itself.

**Architecture:** A separate runner process (`python -m smos.bench`) grows a throwaway store through preset sizes, measures in-process phases (ingest, per-stage timing, HNSW recall) and HTTP phases (startup, latency, concurrency sweep, mixed load) against its own loopback server, and writes a progress file and a result JSON. A `BenchmarkService` in the live app starts and supervises it, holds the `/api/v1` maintenance flag, stores the result in a new admin-DB table, and deletes everything. Admin routes and templates render the page and report; charts are inline SVG.

**Tech Stack:** Python, FastAPI/Jinja2 (existing), FAISS, psutil, stdlib `http.client` and `subprocess`, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-29-benchmark-design.md`

## Global Constraints

- No new dependency and no new `/api/v1` route (httpx is a dev-only dependency, so the load client uses stdlib `http.client` in threads; plan call).
- While a run is active every `/api/v1` path except `/api/v1/health` answers 503 with `Retry-After`; health stays 200. Separate flag `app.state.benchmark_active`; `app.state.maintenance` (update restart) keeps its meaning.
- The real store, tokens and usage log are never touched; the runner works only in `<SMOS_DATA_DIR>/bench-tmp/<run id>/`.
- Deletion only ever happens under `<data_dir>/bench-tmp/`, by exact path built from an integer run id; never a pattern over the data directory.
- Admin DB migration 5 (`benchmark_runs`); never edit migrations 1-4. Keep the newest 50 runs.
- Presets: Quick 500/1k/2.5k/5k, step 8 s, clients 1,2,4,8; Standard 1k/5k/10k/25k, step 15 s, clients 1,2,4,8,16,32,64; Full 1k/5k/10k/25k/50k/100k, step 20 s, same clients as Standard. Latency budget default 200 ms (p95).
- HNSW: `IndexHNSWFlat` M=32, efConstruction=200, efSearch=64; recall@10 and recall@50 against exact search.
- Verdict: *not_needed* (p95 at largest size within budget and projected size at budget >= 3x largest measured), *watch* (within budget, projection < 3x), *consider* (p95 over budget and vector search >= 25% of query time), *not_hnsw* (over budget, vector search < 25%; names the dominant stage). Quick preset: "range too small" note.
- Comparison flags: more than 20% worse = `regression`, 10-20% worse = `slower`, else `ok`.
- A concurrency step is `client_limited` when the runner's own CPU share is above 85%; such steps do not count toward peak.
- Start refuses while a run, an update, or an update restart is active; waits up to 30 s for in-flight requests, else clears the flag and refuses.
- A run starts only from the admin panel (spec 7.2): `python -m smos.bench` exits 2 unless the environment variable `SMOS_BENCH_LAUNCH_SECRET` hashes (SHA-256, hex) to the content of `<work_dir>/launch.sha256` and `work_dir.parent.name == "bench-tmp"`; the service creates the secret per run (`secrets.token_urlsafe(32)`), passes it only through the child's environment, never on the command line or in logs.
- Admin routes use `admin_required`; POSTs check CSRF with `_check_csrf`. Print-to-PDF only (no server PDF route).
- Test command: `rtk proxy python -m pytest tests -q`. Use the Edit/Write tools for text containing backslashes. No company, host or CA names anywhere.
- A change under `smos/` ships as a release (0.3.3 or higher); do not bump the version or tag in this plan.

## Review Focus

- Start pressed twice or from two tabs: the second is refused server-side, the first run is unaffected (Task 8).
- The live service restarts mid-run: no stuck maintenance, `running` rows become `interrupted`, `bench-tmp` is removed (Task 8).
- Too little free disk or RAM: refusal with a clear message and the flag never set (Task 8).
- Report page for a failed, cancelled or interrupted run (no or partial result) and for a result with an older `schema`: renders, no 500 (Task 10).
- Odd form input and ids: unknown preset, zero/negative/non-numeric budget, missing CSRF, unknown or non-integer run id: 400/403/404, never a traceback or a started run (Task 10).

---

### Task 1: Statistics helpers

**Files:**
- Create: `smos/bench/__init__.py` (empty), `smos/bench/stats.py`
- Test: `tests/test_bench_stats.py`

**Interfaces:**
- Produces: `percentile(values: list[float], q: float) -> float` (linear interpolation, q in 0..100, `ValueError` on empty); `summarize_latencies(ms: list[float]) -> dict` with keys `n, mean, p50, p95, p99, max` (all `None` except `n=0` when empty); `peak_throughput(steps: list[dict]) -> Optional[dict]` and `sustainable_step(steps: list[dict], budget_ms: float) -> Optional[dict]` where a step dict has `clients, rps, p95_ms, errors, requests, client_limited`; `find_knee(steps: list[dict]) -> Optional[int]` (client count after which rps grows less than 10% per doubling); `project_size_at_budget(points: list[tuple[float, float]], budget_ms: float) -> Optional[float]` (straight-line least squares of p95 against segments, `None` with fewer than 3 points or non-positive slope).

- [ ] **Step 1: Write failing tests** `test_percentile_interpolates`, `test_percentile_empty_raises`, `test_summarize_empty_has_n_zero`, `test_peak_ignores_client_limited_and_errored_steps` (a step with `errors > 0` is excluded from sustainable but included in peak only if not client-limited), `test_sustainable_requires_zero_errors_and_p95_within_budget`, `test_knee_found_when_growth_flattens`, `test_knee_none_when_always_growing`, `test_projection_linear` (points `(1000, 10), (2000, 20), (3000, 30)`, budget 100 -> `10000`), `test_projection_none_when_flat_or_short`.
- [ ] **Step 2: Run** `rtk proxy python -m pytest tests/test_bench_stats.py -q`; expect FAIL (module missing).
- [ ] **Step 3: Implement** the functions above in `smos/bench/stats.py`; pure Python, no numpy needed.
- [ ] **Step 4: Run the tests**; expect PASS.
- [ ] **Step 5: Commit** the spec, this plan, and the new files: `git add docs/superpowers/specs/2026-10-29-benchmark-design.md docs/superpowers/plans/2026-10-29-benchmark.md smos/bench tests/test_bench_stats.py` then `git commit -m "feat(bench): statistics helpers; benchmark design and plan"`.

---

### Task 2: Verdict, comparison and metric definitions

**Files:**
- Create: `smos/bench/analysis.py`
- Test: `tests/test_bench_analysis.py`

**Interfaces:**
- Consumes: `project_size_at_budget`, `summarize_latencies` from Task 1.
- Produces: `hnsw_verdict(result: dict, budget_ms: float) -> dict` returning `{"level": "not_needed"|"watch"|"consider"|"not_hnsw"|"insufficient", "headline": str, "facts": dict}`; `compare_runs(current: dict, previous: Optional[dict]) -> list[dict]` rows `{"key","label","previous","current","change_pct","flag"}` (flag in `ok|slower|regression|better|n/a`); `key_metrics(result: dict) -> dict[str, float]`; `METRIC_DEFINITIONS: dict[str, str]`. Result dict shape (the contract with Task 6/7): `scaling: [{"segments", "ingest": {"seg_per_s"}, "stages": {"embed_ms","vector_ms","bm25_ms","graph_ms","sqlite_ms","fusion_ms","total_ms"}, "hnsw": {"recall10","recall50","build_s","mem_bytes","query_ms","flat_ms"}, "latency": {"query": {"p50","p95","p99"}}, "startup_s", "disk_bytes", "index_bytes", "rss_bytes"}]`, `sweep: [step...]`, `mixed: {"read_p95_alone_ms","read_p95_with_writer_ms"}`.

- [ ] **Step 1: Write failing tests**, one per verdict branch with hand-built `result` dicts: `test_verdict_not_needed`, `test_verdict_watch_when_projection_under_three_times`, `test_verdict_consider_when_over_budget_and_vector_share_high`, `test_verdict_not_hnsw_names_dominant_stage` (embedding), `test_verdict_insufficient_with_two_sizes`, `test_verdict_facts_include_recall_and_delete_caveat`; and `test_compare_flags_regression_over_20_percent`, `test_compare_slower_between_10_and_20`, `test_compare_higher_is_better_for_throughput` (req/s drop is the regression), `test_compare_without_previous_is_na`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** `smos/bench/analysis.py` with the thresholds as module constants (`PROJECTION_HEADROOM = 3.0`, `VECTOR_SHARE_MIN = 0.25`, `REGRESSION_PCT = 20.0`, `SLOWER_PCT = 10.0`). `METRIC_DEFINITIONS` has one sentence for every metric the report shows (peak, sustainable, knee, p50/p95/p99, cold/warm, stage names, recall@k, hit@k, MRR, startup, ingest rate).
- [ ] **Step 4: Run**; expect PASS. Mutation-check: change a threshold constant, see the matching test fail, revert.
- [ ] **Step 5: Commit** `feat(bench): HNSW verdict, run comparison, metric definitions`.

---

### Task 3: Generated corpus and probe queries

**Files:**
- Create: `smos/bench/corpus.py`
- Test: `tests/test_bench_corpus.py`

**Interfaces:**
- Produces: `class CorpusBuilder(seed: int)` with `next_documents(target_segments: int) -> list[dict]` (JSON-compatible `IngestionPayload` dicts; returns only documents not yet issued, until the total issued segments reach `target_segments`; `external_handle.system == "bench"`, native ids `BD-000001...`) and `probes(n: int = 200) -> list[Probe]`; `@dataclass Probe(query: str, marker: Optional[str])` (a needle probe has `marker` = the unique id such as `REQ-004711` that must appear in a correct result text; topical probes have `marker=None`). Segments per document 20-60; paragraphs 40-150 words, one paragraph block per intended segment, a heading block per document; `source_links` between documents using an edge type from `smos.ingest.adapters.registry.all_edge_types()`; 1-2 `domain_tags` from a small tag set.

- [ ] **Step 1: Write failing tests**: `test_same_seed_same_documents`, `test_different_seed_differs`, `test_incremental_issue_has_no_overlap_or_gaps` (call with 100 then 300: handles unique, total reaches >= 300), `test_documents_pass_validate_payload` (build `IngestionPayload(**doc)` and assert `validate_payload` returns `[]` for 50 documents), `test_needle_marker_present_in_issued_text` (every needle probe's marker appears in the text of an issued document once the corpus is large enough), `test_segment_ids_unique`, `test_probe_queries_long_enough` (>= the API minimum input chars, see `_MIN_INPUT_CHARS` in `smos/api/routes.py`).
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement.** Topic vocabularies: a few dozen topics, each a list of ~40 domain-like words (emissions, braking, firmware, test, interface, timing, ...); a segment draws mostly from one topic plus a few from a second so vectors cluster. Use `random.Random(seed)` only; no global state. Needle probes: marker plus 5-8 words sampled from the target segment, formed as a natural query string. Probes only reference segments from the first `1000` issued so they exist at every preset size.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `feat(bench): deterministic synthetic corpus and probe queries`.

---

### Task 4: Report charts

**Files:**
- Modify: `smos/admin/charts.py` (append functions), `smos/admin/templates/base.html` (CSS for new classes and print rules)
- Test: `tests/test_bench_charts.py`

**Interfaces:**
- Consumes: `Series`, `_empty`, `_nice_ceiling`, `_fmt_value`, margins from `charts.py`.
- Produces: `xy_chart(series: list[Series], *, title: str, x_label: str, y_unit: str = "", x_ticks: Optional[list[float]] = None, x_log: bool = False, reference: Optional[tuple[str, float]] = None, marker: Optional[tuple[str, float]] = None, width: int = 640, height: int = 220) -> str` (numeric x axis, ticks labelled with the compact value, e.g. `5k`); `stacked_bar(rows: list[tuple[str, list[tuple[str, float]]]], *, title: str, unit: str = "ms", width: int = 640) -> str` (one horizontal stacked bar per row, segment labels in a legend, percent in the `<title>`); `data_table(headers: list[str], rows: list[list[str]]) -> str` (escaped HTML table used under each chart). All return inline SVG/HTML, escape every caller string, need no script.

- [ ] **Step 1: Write failing tests**: `test_xy_chart_is_svg_with_title_and_all_series`, `test_xy_chart_log_x_places_ticks_in_order`, `test_xy_chart_marker_and_reference_drawn`, `test_xy_chart_empty_says_no_data`, `test_xy_chart_escapes_series_names` (a name containing `<script>` appears escaped), `test_stacked_bar_segments_sum_to_full_width`, `test_stacked_bar_zero_total_is_empty_not_error`, `test_data_table_escapes_cells`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** the three functions in `charts.py`, reusing helpers and CSS classes (`line s0..s3`, `grid`, `axis-label`, `reference`); add colour classes `s4`, `s5` and `.bar.s0..s5` plus a `.marker` style to `base.html`.
- [ ] **Step 4: Run** `rtk proxy python -m pytest tests/test_bench_charts.py tests/test_charts.py -q`; expect PASS.
- [ ] **Step 5: Commit** `feat(admin): numeric-axis and stacked-bar charts for the benchmark report`.

---

### Task 5: Storage — migration 5 and run repository

**Files:**
- Modify: `smos/admin/db.py` (add `_add_benchmark_runs`, append `(5, "benchmark_runs", ...)` to `ADMIN_MIGRATIONS`)
- Create: `smos/admin/benchmarks.py`
- Test: `tests/test_bench_storage.py`; update the admin DB version assertion in `tests/test_migrations.py` if it pins 4.

**Interfaces:**
- Produces in `benchmarks.py` (all take an open sqlite connection; callers hold `app.state.admin_lock`): `create_run(conn, *, preset: str, budget_ms: float, version: str) -> int` (status `running`); `finish_run(conn, run_id: int, *, status: str, result: Optional[dict], hardware: Optional[dict], error: str = "") -> None` (status in `completed|failed|cancelled|interrupted`); `get_run(conn, run_id: int) -> Optional[dict]` (JSON columns parsed); `list_runs(conn, limit: int = 50) -> list[dict]` (no `result` body, only headline numbers needed by the history table); `previous_completed(conn, preset: str, before_id: int) -> Optional[dict]`; `mark_running_interrupted(conn) -> int`; `prune(conn, keep: int = 50) -> int`; `has_running(conn) -> bool`.

- [ ] **Step 1: Write failing tests**: migration creates the table on a fresh DB and on a DB at version 4 with existing rows in other tables intact; `test_create_and_finish_roundtrip`; `test_result_json_roundtrips_and_keeps_schema_field`; `test_previous_completed_same_preset_only_and_older_only`; `test_mark_running_interrupted_only_touches_running`; `test_prune_keeps_newest_50`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** migration (`id, started_at, finished_at, status, preset, budget_ms, smos_version, hardware_json, result_json, error`) and the repository functions.
- [ ] **Step 4: Run** `rtk proxy python -m pytest tests/test_bench_storage.py tests/test_migrations.py -q`; expect PASS.
- [ ] **Step 5: Commit** `feat(admin): benchmark_runs table (migration 5) and repository`.

---

### Task 6: Runner phases

**Files:**
- Create: `smos/bench/presets.py`, `smos/bench/phases.py`, `smos/bench/httpload.py`
- Test: `tests/test_bench_phases.py`, `tests/test_bench_httpload.py`

**Interfaces:**
- Consumes: `CorpusBuilder`, `Probe` (Task 3); `summarize_latencies`, `find_knee` (Task 1); `VectorStore`, `do_ingest_document_schema`, `embed` (existing).
- Produces in `presets.py`: `@dataclass Preset(name, sizes: list[int], step_seconds: float, clients: list[int], probe_count: int)`, `PRESETS: dict[str, Preset]` (quick, standard, full; values from Global Constraints) and `SMOKE: Preset` (sizes `[40, 90]`, step 1.5 s, clients `[1, 2]`, probes 8; used by tests and the verification run, not offered in the UI).
- Produces in `phases.py` (in-process, store open, no server): `ingest_to(store, builder: CorpusBuilder, target_segments: int) -> dict` (`seg_per_s, seconds, segments, quarantined`; quarantined documents must be 0 or reported); `profile_stages(store, probes: list[Probe], repeats: int = 3) -> dict` (the `stages` shape of Task 2: times the real steps of `build_context` by calling the same store/index/BM25/graph functions directly; no production code hooks); `compare_hnsw(store, probes, k_values=(10, 50)) -> dict` (builds `faiss.IndexHNSWFlat(384, 32)` from the store's own vectors via `store._index`, same normalised queries; returns the `hnsw` shape of Task 2); `store_footprint(path: Path) -> dict` (`disk_bytes`, `index_bytes`).
- Produces in `httpload.py` (stdlib only): `class BenchServer(data_dir: Path, port: int, token: str)` with `start(timeout: float = 120) -> float` (seconds until `/api/v1/health` is 200; launches `python -m smos.api.main` with `SMOS_DATA_DIR`, `SMOS_HOST=127.0.0.1`, `SMOS_PORT`, `SMOS_SAMPLE_INTERVAL_S=3600`), `stop()` (terminate then kill the process tree), `pid`; `quality_probes(base_url, token, probes, k=10) -> dict` (`hit1, hit10, mrr`, needle probes only, marker looked up in result texts); `latency_probe(base_url, token, probes, n: int) -> dict` (`cold_ms`, `query` summary, `node_text` summary, `post_doc` summary); `run_step(base_url, token, clients: int, seconds: float, probes) -> dict` (`clients, requests, errors, rps, p50_ms, p95_ms, p99_ms, client_cpu_share, client_limited`; each client thread keeps one connection open and loops over probe queries; errors are non-2xx and timeouts); `mixed_step(base_url, token, clients, seconds, probes, builder) -> dict` (`read_p95_alone_ms`, `read_p95_with_writer_ms`, `writer_docs`).

- [ ] **Step 1: Write failing tests** with a small in-process store on a temp dir (the suite already loads the bundled model): `test_ingest_to_reaches_target_and_reports_rate`, `test_ingest_to_is_incremental` (second call to a larger target adds only the difference), `test_profile_stages_sums_close_to_total` (parts add to within 10% of `total_ms`), `test_compare_hnsw_recall_between_0_and_1_and_exact_is_one` (recall of flat against itself is 1.0), `test_store_footprint_counts_files`. For `httpload`, use a tiny stdlib `http.server` stub in a thread: `test_run_step_counts_requests_and_errors`, `test_run_step_flags_client_limited_when_cpu_share_high` (inject the CPU reading), `test_quality_probes_hit_and_mrr_from_stub_results`, `test_bench_server_start_returns_after_health_and_stop_kills_tree` (real `python -m smos.api.main` on a temp dir with an admin created via `open_admin_db` + `accounts.bootstrap_admin`; asserts the pid is gone after `stop()`).
- [ ] **Step 2: Run** `rtk proxy python -m pytest tests/test_bench_phases.py tests/test_bench_httpload.py -q`; expect FAIL.
- [ ] **Step 3: Implement** the three modules. Keep `profile_stages` honest: it must call the same functions `build_context` calls, and a comment names `smos/compression/context_builder.py::build_context` as the thing mirrored; add a test that its candidate ids equal `build_context`'s top ids on a probe, so drift is caught.
- [ ] **Step 4: Run**; expect PASS. Mutation-check the client-limited rule and the incremental-ingest test.
- [ ] **Step 5: Commit** `feat(bench): presets, in-process phases and HTTP load phases`.

---

### Task 7: Runner orchestration and CLI

**Files:**
- Create: `smos/bench/runner.py`, `smos/bench/__main__.py`
- Test: `tests/test_bench_runner.py`

**Interfaces:**
- Consumes: Tasks 1-3 and 6; `read_hardware` from `smos/admin/metrics.py`; `open_admin_db`, `accounts.bootstrap_admin`, `tokens.create_token`.
- Produces: `run(work_dir: Path, preset: Preset, budget_ms: float, seed: int = 1) -> dict` (the full result dict, `schema: 1`, also written atomically to `work_dir/result.json`); progress file `work_dir/progress.json` rewritten atomically with `{"step", "steps", "label", "percent", "updated"}`; CLI `python -m smos.bench --work-dir PATH --preset NAME --budget-ms N [--seed N]` exiting 0 on success, 1 on failure (error text to `work_dir/error.txt` and stderr), and handling SIGTERM/CTRL-C by stopping its server child and exiting 130. `main()` first calls `check_launch_authorisation(work_dir: Path, environ: Mapping[str, str]) -> None` (raises `SystemExit(2)` with "benchmarks are started from the admin page" per Global Constraints); `run()` itself is a library function without the check (tests call it directly), and the CLI check is the only entry-point guard. Per-size order exactly as the spec table: ingest, stage profile, HNSW, close store, create the loopback token and start `BenchServer`, quality probes, latency probe, then at the largest size only the concurrency sweep and the mixed step, stop the server. Result adds `hardware` (from `read_hardware` plus CPU model, FAISS and Python versions, embedding model name), `warnings` (client-limited steps, skipped phases), `started_at`, `finished_at`, `preset`, `seed`, `sizes`.

- [ ] **Step 1: Write failing tests** using `SMOKE`: `test_run_smoke_produces_complete_result` (every key the analysis and report need exists; `scaling` has two entries; `sweep` has two steps; JSON-serialisable), `test_progress_file_reaches_100_percent`, `test_runner_leaves_no_server_process` (psutil: no child of the test process remains), `test_cli_exit_code_and_error_file_on_failure` (unknown preset with valid authorisation -> exit 1 and `error.txt`), `test_cli_refuses_without_launch_secret` / `test_cli_refuses_wrong_secret` / `test_cli_refuses_work_dir_outside_bench_tmp` (exit 2, nothing written to the work dir), `test_cli_accepts_matching_secret` (helper in the test creates `bench-tmp/1/launch.sha256`), `test_same_seed_same_corpus_ids` (two smoke runs issue the same handles).
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement.** The runner must not import the live app or open anything outside `work_dir`; assert at start that `work_dir` is empty or absent.
- [ ] **Step 4: Run** `rtk proxy python -m pytest tests/test_bench_runner.py -q`; expect PASS (about a minute).
- [ ] **Step 5: Commit** `feat(bench): runner orchestration and CLI`.

---

### Task 8: BenchmarkService, maintenance gate, updater interlock, cleanup

**Files:**
- Create: `smos/bench/service.py`
- Modify: `smos/api/app.py` (create the service, set `app.state.benchmark_active = False`, extend `maintenance_gate`, call recovery at startup, stop service in `close`), `smos/update/apply.py` (`Updater.begin` refuses while a run is active)
- Test: `tests/test_bench_service.py`, `tests/test_bench_maintenance.py`, `tests/fake_bench_runner.py` (a small script used as the runner command in tests)

**Interfaces:**
- Consumes: Task 5 repository; `psutil`; `ApplyRefused`.
- Produces: `class BenchmarkRefused(Exception)` (message safe to show an admin); `class BenchmarkService(app, data_dir: Path, command_factory: Callable[[Path, str, float], list[str]] = default_command, drain_timeout: float = 30.0, min_free_bytes: Callable[[Preset], int] = ..., min_free_ram: Callable[[Preset], int] = ...)` with `start(admin: str, preset: str, budget_ms: float) -> int`, `cancel() -> bool`, `status() -> dict` (`state` idle|running|finishing, `run_id`, `progress`, `started_at`), `recover_on_startup() -> None`, `close() -> None`; helper `safe_run_dir(data_dir: Path, run_id: int) -> Path` raising `ValueError` unless the result is exactly `<data_dir>/bench-tmp/<int>`; `app.state.bench = service`.
- Behaviour: start order is validate preset and budget (`0 < budget <= 60000`), refuse if a run/update/`maintenance`/`closing`, check free disk and RAM (refuse before setting any flag), set `benchmark_active`, wait for `app.state.inflight == 0` up to `drain_timeout` (else clear and refuse), insert the `running` row, create the launch secret and write its SHA-256 to `<run dir>/launch.sha256`, spawn the runner (new process group, secret only in its environment), watch it in a thread. The watcher, in a `finally`: read `result.json` if present, store the row (`completed`/`failed`/`cancelled`) and prune, kill the whole process tree, delete the run directory with `safe_run_dir`, verify the directory is gone and no child remains (record `cleanup` = `"verified"` or the leftover list in the stored result and log a warning), clear `benchmark_active`. `recover_on_startup` marks `running` rows `interrupted` and removes every directory under `bench-tmp/` (entries must be directories named as integers; anything else is left alone and logged).
- Wall-time cap: the watcher ends a run that exceeds `wall_cap_s(preset)` (default 4x the preset's estimate: Quick 20 min, Standard 90 min, Full 6 h), status `failed`, error naming a smaller preset; test `test_wall_time_cap_fails_run_and_cleans_up` with a tiny injected cap.
- Gate: in `maintenance_gate`, when `app.state.benchmark_active` and the path starts with `/api/v1` and is not `/api/v1/health`, return 503 JSON `{"detail": "SMOS is running a benchmark, try again shortly"}` with `Retry-After: 30`.

- [ ] **Step 1: Write failing tests.** Gate: `test_every_openapi_route_returns_503_during_benchmark_except_health` (enumerate `app.openapi()` paths plus `/api/v1/openapi.json` and `/api/v1/docs` with a valid token; assert 503, health 200); `test_admin_pages_still_served_during_benchmark`. Service with the fake runner: `test_start_runs_and_stores_completed_result`, `test_second_start_is_refused_while_running` (Review Focus 1), `test_start_refused_when_update_in_progress` and `test_updater_refuses_while_benchmark_active`, `test_start_refused_when_disk_or_ram_low_and_flag_never_set` (Review Focus 3), `test_drain_timeout_clears_flag_and_refuses`, `test_cancel_ends_maintenance_and_removes_directory`, `test_runner_crash_marks_failed_and_cleans_up`, `test_runner_killed_externally_cleans_up`, `test_cleanup_kills_grandchild_processes` (the fake runner spawns a sleeping grandchild; psutil shows none after cancel), `test_cleanup_failure_is_recorded_not_silent` (make deletion fail once: result carries a cleanup warning, flag still cleared), `test_recover_on_startup_marks_interrupted_and_removes_stale_dirs` (Review Focus 2), `test_safe_run_dir_rejects_traversal_and_non_int` (`"../x"`, `"1/../../data"`, a symlink named `1` pointing outside), `test_recover_leaves_foreign_entries_alone`.
Also `test_launch_secret_in_env_not_in_argv_or_logs` (the fake runner records its argv and environment; the secret is absent from argv and from captured log output; its hash matches `launch.sha256`).
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** `service.py`, wire it in `app.py`, add the refusal in `Updater.begin` (`ApplyRefused("A benchmark is running; wait for it to finish or cancel it.")`) and make `start` consult `service.updater.state()`.
- [ ] **Step 4: Run** `rtk proxy python -m pytest tests/test_bench_service.py tests/test_bench_maintenance.py tests/test_api_auth.py tests/test_api_scopes.py -q`; expect PASS. Mutation-check: make `safe_run_dir` skip its check and the gate skip a path; watch the tests fail; revert.
- [ ] **Step 5: Commit** `feat(bench): service, /api/v1 maintenance gate, updater interlock, guaranteed cleanup`.

---

### Task 9: Report view model

**Files:**
- Create: `smos/admin/benchmark_view.py`
- Test: `tests/test_bench_view.py`

**Interfaces:**
- Consumes: Tasks 2, 4, 5.
- Produces: `build_report(run: dict, previous: Optional[dict]) -> dict` returning `{"run", "tiles": [{"key","label","value","hint","change"}], "verdict", "charts": [{"key","title","definition","svg","table"}], "comparison": rows, "details": {...}, "warnings": [...], "complete": bool}`; it must tolerate a missing or partial `result` (`complete=False`, only the sections that exist), an unknown future `schema` (shows a note and the raw run details) and missing individual keys. Charts per spec section 6: latency vs size with budget reference; throughput and p95 vs clients with the knee marked; stage breakdown (largest size); exact vs HNSW time and recall; ingest rate, startup, index size/disk, RAM vs size; mixed load; quality probes labelled "synthetic". Also `history_rows(runs: list[dict], previous_lookup) -> list[dict]` for the page table (headline numbers, verdict level, vs-previous text).

- [ ] **Step 1: Write failing tests** with a hand-built complete result fixture (stored under `tests/fixtures/` or built in the test): `test_report_has_tiles_verdict_and_all_charts`, `test_each_chart_has_definition_and_data_table`, `test_partial_result_renders_without_error` (Review Focus 4: only `scaling[0].ingest`), `test_failed_run_without_result_gives_incomplete_report_with_error`, `test_unknown_schema_version_degrades_gracefully`, `test_comparison_rows_present_when_previous_exists`, `test_quality_chart_is_labelled_synthetic`, `test_history_rows_for_cancelled_run`.
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** `benchmark_view.py`; every number goes through the formatting helpers of `charts.py`; no raw HTML from stored strings (escape in templates).
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `feat(admin): benchmark report view model`.

---

### Task 10: Admin routes, templates, print stylesheet

**Files:**
- Modify: `smos/admin/pages.py` (routes), `smos/admin/templates/base.html` (nav link "Benchmark"; the `@media print` rules live in the report template's `head` block)
- Create: `smos/admin/templates/benchmark.html`, `smos/admin/templates/benchmark_report.html`
- Test: `tests/test_bench_pages.py`

**Interfaces:**
- Consumes: `request.app.state.bench` (Task 8), `benchmark_view` (Task 9), `benchmarks` repository (Task 5).
- Produces routes (all `admin_required`; the two POSTs check CSRF): `GET /admin/benchmark` (Run panel with preset radios, budget field, free-disk/RAM estimate, progress panel and Cancel while running, history table; a `<meta http-equiv="refresh" content="3">` only while a run is active), `POST /admin/benchmark/start` (303 to the page; refusals re-render with status 409 and the message), `POST /admin/benchmark/cancel`, `GET /admin/benchmark/status` (JSON, `Cache-Control: no-store`), `GET /admin/benchmark/{run_id:int}` (report, 404 when unknown), `GET /admin/benchmark/{run_id:int}.json` (download with `Content-Disposition`). Report page: tiles, verdict box, charts each followed by a `<details>` data table, comparison table, run details, **Export PDF** button calling `window.print()`.
- Print rules (report template): hide `header`, buttons, `.no-print`; expand all `<details>`; `break-inside: avoid` on charts and tiles; light colours; a running header block (run id, date, version, preset) repeated via `thead`-style table or a fixed element; `@page` margins.

- [ ] **Step 1: Write failing tests** with a fake service injected on `app.state.bench` and stored runs: `test_anonymous_redirected_to_login` (all routes), `test_page_shows_presets_budget_and_history`, `test_start_requires_csrf` (403), `test_start_unknown_preset_400`, `test_start_bad_budget_400` (`""`, `0`, `-5`, `abc`, `1e9`), `test_start_calls_service_and_redirects`, `test_start_refusal_shows_message_409`, `test_cancel_requires_csrf`, `test_status_json_shape_and_no_store`, `test_report_renders_charts_and_export_button`, `test_report_unknown_id_404` and `test_report_non_integer_id_404_not_500`, `test_report_for_failed_run_renders` (Review Focus 4 and 5), `test_json_download_headers`, `test_print_rules_hide_navigation_and_controls` (the response contains an `@media print` block that hides `header` and buttons and expands details), `test_nav_has_benchmark_link`, and the start-only-from-admin guards: `test_no_openapi_route_mentions_benchmark`, `test_bearer_token_cannot_start_a_run` (valid rw token on `POST /admin/benchmark/start` is redirected to login and the fake service is never called), `test_get_on_start_url_is_405`, `test_start_has_no_other_caller` (a source scan of `smos/` finds `.start(` of the benchmark service only inside the start route handler; mutation-check by adding a second caller).
- [ ] **Step 2: Run**, expect FAIL.
- [ ] **Step 3: Implement** routes and templates following `update.html` / `dashboard.html` style; use `run_in_threadpool` or plain `def` handlers for anything that locks.
- [ ] **Step 4: Run** `rtk proxy python -m pytest tests/test_bench_pages.py tests/test_admin_pages.py tests/test_dashboard_page.py -q`; expect PASS.
- [ ] **Step 5: Commit** `feat(admin): Benchmark page, report and print-to-PDF export`.

---

### Task 11: Real runs, verification record, documentation

**Files:**
- Create: `docs/benchmark-verification.md`
- Modify: `ARCHITECTURE.md` (section 4 latency figures, after measured), `docs/hnsw-assessment.md` (pointer to measured results), `docs/INSTALL.md` (one paragraph: what the Benchmark page does, that it needs a few GB of free disk and an extra embedding-model's worth of RAM, that the API is unavailable during a run), `docs/HANDOFF.md`
- Test: full suite

- [ ] **Step 1: Full suite** `rtk proxy python -m pytest tests -q`; expect all pass (779 before plus the new tests).
- [ ] **Step 2: Real end-to-end on the dev PC.** Start `python -m smos.api.main` on a temp data dir with an admin account (stop it afterwards), log in with a browser, run **Quick** from the page. Record: duration, the report screenshot description, the verdict, tiles; confirm during the run that `/api/v1/documents` returns 503 and `/api/v1/health` returns 200; confirm after it that `bench-tmp/` is empty, no `python -m smos.api.main` child remains, and the real store file sizes and counts are unchanged. Repeat once with **Cancel** pressed mid-run and once by killing the service mid-run then restarting (maintenance gone, row `interrupted`, directory gone).
- [ ] **Step 3: Run Standard** on the dev PC and record the numbers; run Standard twice (the second run also exercises the comparison table) to calibrate the 10/20% thresholds and the run-to-run noise; adjust constants only with the evidence written in the record.
- [ ] **Step 4: PDF check.** Use the Export PDF button in the browsers the Governor uses (ask which), save the PDF, and record that charts are not cut and tables are complete.
- [ ] **Step 5: Write `docs/benchmark-verification.md`** with sections: what was run, results, cleanup evidence, threshold calibration, PDF check, known gaps (the Debian run is the Governor's: list the steps), and update `ARCHITECTURE.md` section 4 with measured dev-PC figures labelled as such. Update `docs/HANDOFF.md`.
- [ ] **Step 6: Commit** `docs: benchmark verification record, architecture and install notes`.

---

### Task 12: Codex review package

**Files:**
- Create: `docs/superpowers/review/2026-10-29-7-review-package.md`

- [ ] **Step 1: Write the package** in the same form as `docs/superpowers/review/2026-10-29-6-review-package.md`: header naming the diff range (`git diff <spec-commit-parent>..HEAD`), design and plan paths; **1. Scope** (files and tests); **2. Run it** (install note, `rtk proxy python -m pytest tests -q`, expected passed count, how to run the smoke preset through the test helper, and the fact that `python -m smos.bench` refuses to run outside the admin panel); **3. What to attack, hardest first**: (1) the maintenance gate: any `/api/v1` path that answers during a run, health exception, `openapi`/`docs`, and request races around `inflight` and the flag; (2) cleanup: every exit route, process-tree kill on Windows and Linux, `safe_run_dir` bypasses (symlinks, case, trailing dots, `bench-tmp` itself), what happens when deletion fails, crash/restart recovery; (3) the updater interlock and the snapshot size effect of a leftover `bench-tmp`; (4) measurement honesty: `profile_stages` drifting from `build_context`, HNSW recall methodology, projection maths, verdict thresholds, client-limited detection, cold/warm definitions, anything that lets a number look better than it is; (5) start-only-from-admin: any way to start a run without an admin session (API, token, GET, CLI, environment, race on the launch secret); (6) report and pages: stored-data escaping (XSS via stored strings), CSRF, partial/old-schema results, print stylesheet; (7) resource safety: the runner's RAM/disk use against the checks, wall-time cap, runaway loops; **4. Known limits and honest gaps** (synthetic text, single process, Debian run pending, thresholds calibrated on few runs, PDF via print only); **5. How to report** (per finding: severity, file and line, scenario, fix; write to `docs/superpowers/review/2026-10-29-7-review.md`).
- [ ] **Step 2: Fill in the real numbers** (test count, commit range) from the repository; verify every file named in the package exists (`ls`).
- [ ] **Step 3: Commit** `docs: Codex review package 7 (benchmark)`.
- [ ] **Step 4: Write the session handoff** with the `handoff` skill (what shipped, what is pending for the Governor: Debian run, the release 0.3.3+ decision, the Codex review).

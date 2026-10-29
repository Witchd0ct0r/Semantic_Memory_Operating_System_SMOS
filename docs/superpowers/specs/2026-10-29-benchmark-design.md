# Benchmark page — design (2026-10-29)

Status: draft for the Governor's approval. Brainstormed with the Governor on 2026-10-29; the page and report layout was
shown as a mockup and approved. Items marked **(my call)** are decisions the builder or the orchestrator made without asking.

## 1. Intent

The Governor installs SMOS on a machine (a Debian host in Docker) and needs to know what *that machine* can do, and whether
the architecture needs to change as the database grows. Today this is answered by hand-run scripts (`tests/bench_*.py`,
`tests/scale_s*.py`) whose results go stale and which nobody can run from where SMOS is installed.

The deliverable is a **Benchmark page in the admin area**: the Governor presses *Start*, SMOS measures itself on its own
hardware, and the result is a readable report with charts that stays in the page's history, so releases and machines can be
compared.

The report answers four questions:

1. **Capacity:** what is the maximum number of requests per second, and how many can it sustain while staying fast?
2. **Load:** how does latency change as parallel traffic rises?
3. **Scale:** how do query speed, ingest speed, startup time, disk and RAM change as the database grows?
4. **HNSW:** is exact search still the right choice on this device, and when would it stop being? (Background:
   `docs/hnsw-assessment.md`, which measured with random vectors and says a real decision needs real embeddings on the target
   host.)

## 2. Goals, non-goals, anti-goals

**Goals**
- One button starts a run from the admin page; progress is visible; the run can be cancelled.
- The report is readable on the same page, with charts drawn by SMOS itself (inline SVG, no external assets, like the
  dashboard).
- Every run is stored with the SMOS version and hardware details and is shown against the previous comparable run, with
  regressions flagged.
- **A run can only be started from the admin panel** (requirement from the Governor): by a logged-in admin session pressing
  *Start* (POST with CSRF). No API route, no bearer token, no GET request, no scheduler and no command line starts one. See 7.2.
- A report can be exported as PDF from its page (section 6.1) and downloaded as JSON.
- A run cleans up after itself completely (section 7.1).
- Runs are repeatable: the same size preset on the same machine uses the same generated corpus and the same probe queries.

**Non-goals**
- No scheduled or automatic runs, no CI gate. Runs start only from the button.
- No benchmark on the Governor's real documents (decided: generated text only).
- No cross-machine comparison view; each installation shows its own history (reports can be downloaded as JSON).
- No measurement of TLS, reverse proxies, or multi-process deployments. SMOS runs as one server process; the benchmark measures
  that.
- No new `/api/v1` endpoints. (Keeps the "every route needs a token" guard and the scope classification untouched.)

**Anti-goals (a run that "works" but fails the purpose)**
- Touching, reading, or modifying the real store, real tokens, or the real usage log.
- Leaving SMOS stuck in maintenance after a crash, a cancel, a restart or a failed run.
- Reporting a number without saying what it measures. Every metric has a one-line definition in the report.
- Presenting results on synthetic text as retrieval-quality claims about the Governor's documents. Absolute quality numbers
  are labelled synthetic; only the *trend across sizes and releases* is meaningful.
- Verdicts that cannot be explained. The HNSW verdict must show the numbers it is based on.

## 3. Decisions made with the Governor

- Content: capacity (max requests/s), latency under traffic, the HNSW question, speed against database size on the installed
  device, plus the extra metrics listed in section 6 (the Governor accepted the recommendations).
- **The service is out of service during a run** ("maintenance mode"): `/api/v1` answers 503 to everyone, the admin pages stay
  up, and `/api/v1/health` keeps answering 200 so the Docker healthcheck does not restart the container.
- The run uses a **throwaway store in a temporary directory**; the real data is never read or changed. Everything is deleted when
  the run ends.
- Test text is **generated**.
- Started from the page; the report is read on the page, with charts.
- Layout as in the approved mockup: a page with a Run panel (size presets, latency budget, start, and while running a progress bar
  and a Cancel button that ends maintenance at once) and a history table; a report page with headline tiles, an HNSW verdict box,
  charts, and a comparison table against the previous run.

## 4. Constraints from the codebase

- The page is served by the same process that would be benchmarked, so the **benchmark runs in a separate process** (own
  embedding model, own throwaway data directory, own server on loopback). The live service only starts it, reads its progress and
  stores its result. This also keeps the load generator out of the live process's GIL and store lock.
- Admin DB schema changes need a **new numbered migration** in `smos/admin/db.py` (currently at version 4, so migration 5); an
  existing migration is never edited.
- Admin pages use `admin_required`; POSTs use CSRF (`_check_csrf`); the page follows `smos/admin/pages.py` conventions and the
  templates directory `smos/admin/templates/`.
- `app.state.maintenance` already exists but means "update restart: writes get 503, admin/claim writes refused". The benchmark
  needs a different state (**all** `/api/v1` except health refused, reads included), so use a separate flag, not the same one.
  The update path (`smos/update/apply.py`) must refuse to apply an update while a benchmark runs, and the benchmark must refuse
  to start while an update is being applied; the in-flight counter (`app.state.inflight`) is available to wait for running
  requests to finish.
- The self-updater snapshots the data directory (`apply.py` checks free disk for it), so the temporary benchmark directory must
  not exist while an update is applied (guaranteed by the mutual refusal above) and must be removed at startup if a crash left
  one behind.
- Search today is `VectorStore` with `faiss.IndexIDMap(IndexFlatIP(384))`, hybrid with BM25 and the graph, in
  `smos/compression/context_builder.py::build_context`; ingestion is `do_ingest_document_schema(payload, store)` (the same
  function the API calls). The embedding model is `all-MiniLM-L6-v2`, loaded from `SMOS_EMBEDDING_MODEL_DIR` in the container.
- Cgroup-aware CPU and memory reading already exists in `smos/admin/metrics.py::read_hardware`; reuse it for the hardware block
  and resource measurements, so container limits are respected on Debian.
- A change under `smos/` ships as a release (0.3.3 or higher). The design adds no new dependency (FAISS, psutil, httpx or stdlib
  HTTP are already available; the builder confirms before using a library), so no rebuilt image is needed.
- Charts: `smos/admin/charts.py::line_chart` is a time-series chart; the report needs a numeric x-axis variant and a stacked
  horizontal bar, in the same style (inline SVG, CSS classes, no scripts needed for rendering).
- No company, host or CA names anywhere (memory `feedback-no-company-data-in-repo`).

## 5. How a run works

### 5.1 Lifecycle

1. The Governor opens `/admin/benchmark`, picks a size preset and a latency budget, and presses *Start*. The page shows what
   it needs (disk, extra RAM) and refuses to start if free disk or available RAM are clearly insufficient **(my call:**
   thresholds are estimates per preset plus a margin; the refusal message says what is missing**)**.
2. The service refuses if a run, an update, or an update restart is already in progress. Otherwise it sets the benchmark
   flag; `/api/v1` (except health) now answers 503 with `Retry-After`. It waits for in-flight requests to drain (up to 30 s;
   if they do not drain, it clears the flag and reports that the run did not start **(my call)**).
3. The service starts the runner as a child process (`python -m smos.bench ...`, working directory
   `<SMOS_DATA_DIR>/bench-tmp/<run id>/`), inserts a `running` row in the admin DB, and the page shows progress read from a
   progress file the runner rewrites atomically at each step. The page refreshes itself while a run is active.
4. When the runner exits, the service reads its result file, stores it, sets the status (`completed`, `failed`,
   `cancelled`), deletes the working directory and clears the flag. This happens in a `finally`-style path so every exit
   route (success, error, cancel, runner crash) ends maintenance.
5. *Cancel* terminates the runner and its server child (the whole process tree), then step 4.
6. Service restart during a run: the child dies with the container. At startup the service marks any `running` row as
   `interrupted`, deletes stale `bench-tmp` directories, and is not in maintenance (the flag lives in memory only).
7. The dashboard's 60-second sampler keeps running during a run **(my call)**: its hardware charts then show the benchmark's
   CPU and RAM spike, and the run's start and end times are in the report so the spike can be explained. The usage log is not
   affected because the runner's server writes to its own throwaway admin database.
8. A hard wall-time cap per preset (several times its estimate) fails a stuck run **(my call)**.

### 5.2 Phases inside the runner

The runner grows **one throwaway store** through the preset's sizes (for example Standard: 1k, 5k, 10k, 25k segments). At each
size it does the following, in this order. Phases that need a server start one on `127.0.0.1` with a random port and a token
created in the throwaway admin database; the real tokens are never involved.

| Phase | Runs at | Through | Measures |
|---|---|---|---|
| Ingest to size | each size | the real ingest function, in documents of 20-60 segments | segments/s for this step, time, store size on disk, index size |
| Stage profile | each size | direct calls, no server running | per-query time split into embedding, vector search, BM25, graph expansion, SQLite fetch and fusion; p50/p95 of each |
| HNSW comparison | each size | direct, same vectors as the store | HNSW build time, memory, query time; recall@10 and recall@50 against exact search on the probe queries; exact search time. Parameters as in `docs/hnsw-assessment.md` (M=32, efSearch=64) **(my call: efConstruction 200)** |
| Quality probes | each size | HTTP | hit@1, hit@10 and MRR for needle queries (section 5.3) |
| Startup | each size | start the server process | seconds from process start to first healthy response; index load time |
| Latency probe | each size | HTTP, one client | cold (first query after start) and warm p50/p95/p99 for `POST /query`; `GET` node text and `POST /documents` (one small doc) as secondary operations |
| Concurrency sweep | largest size only | HTTP | parallel clients stepped (Quick: 1, 2, 4, 8; Standard and Full: 1, 2, 4, 8, 16, 32, 64) with fixed time per step; req/s, p50/p95/p99, errors and timeouts per step |
| Mixed load | largest size only | HTTP | at the "knee" client count, queries with and without one concurrent ingest writer; read latency change |
| Resources | throughout | psutil / cgroup | peak and average CPU and RAM (server and client separately), disk growth |

Why this order: the stage profile and HNSW comparison need the store without a server holding it; restarting the server at each
size gives the startup/index-load measurement for free.

**Load client honesty:** the runner records its own CPU use during sweeps. If the client process is above about 85% of one core
(or of its share), the step is flagged *client-limited* and not counted toward "peak" **(my call)**, so the report never blames
SMOS for the load generator's limit. The client uses the standard library's `http.client` with one keep-alive connection per client thread
(`httpx` is a dev-only dependency and none may be added); the client-CPU check above is what keeps this honest **(my call, set
in the plan)**.

### 5.3 Generated corpus and probes

- Deterministic from a seed stored in the report; same preset and seed give the same documents, so runs are comparable.
- Shaped like the Governor's real use (requirements, standards clauses), **(my call)**: documents of 20-60 segments, each
  segment 40-150 words of topic vocabulary drawn from a few dozen topics (so vectors cluster, as real text does), clause
  numbers, "shall"-style sentences, a unique identifier per segment (for example `REQ-004711`), cross-references between
  segments (so the citation graph has real edges), and domain tags.
- **Probe queries** (fixed per seed, about 200): "needle" queries built from a target segment (its identifier plus a
  paraphrase-style subset of its words) with the target known, so hit@k and MRR can be computed at every size; plus topical
  queries without a single target for the latency and HNSW-recall measurements.
- The corpus is synthetic. The report says so wherever quality figures appear.

### 5.4 Presets

| Preset | Sizes (segments) | Time per sweep step | Rough run time |
|---|---|---|---|
| Quick | 500, 1k, 2.5k, 5k | 8 s | a few minutes |
| Standard | 1k, 5k, 10k, 25k | 15 s | about 20 minutes |
| Full | 1k, 5k, 10k, 25k, 50k, 100k | 20 s | an hour or more |

All values are **my call** and are estimates. Ingest time is dominated by embedding speed on the device, so the page should
show an estimate based on the ingest rate measured in the last completed run when there is one, and label the static figure
as approximate when there is not. The final tuning of sizes happens when the builder first runs it on the dev PC and on the
Debian host.

## 6. What the report contains

**Headline tiles:** peak throughput (req/s), sustainable throughput (highest step with zero errors and p95 within the budget,
and its client count), query p95 at the largest size (with p50/p99), ingest rate. Each tile shows the change against the
previous comparable run.

**HNSW verdict box** (rules are **my call**, thresholds adjustable in one place in code):
- *Not needed on this device* when p95 at the largest measured size is within the budget **and** the projected size at which
  the budget would be reached is at least three times the largest measured size.
- *Watch it* when p95 is within the budget but the projected size is under three times the largest measured size.
- *Consider it* when p95 exceeds the budget at a measured size **and** vector search is at least 25% of the query time.
- *HNSW would not fix this* when the budget is exceeded but vector search is under 25% of the query time; the box names the
  dominant stage (for example embedding) instead.

The projection is a straight-line fit of p95 against segment count over the measured sizes, stated as an extrapolation. The box
always shows the numbers it used: vector search share of query time, p95 against budget, projected size, measured HNSW recall,
HNSW build time and memory, and the standing caveat that HNSW cannot delete vectors in place while SMOS deletes on every
re-ingest. With the Quick preset the box says the size range is too small for a reliable projection.

**Charts** (each with a "show data" table beneath it for accessibility and copy-paste, and a one-line definition):
1. Query latency (p50/p95/p99) against database size, with the budget as a reference line.
2. Throughput and p95 against parallel clients, with the knee marked.
3. Where a query spends its time (stacked bar per size, or for the largest size).
4. Exact search against HNSW: query time against size, and recall.
5. Ingest rate, startup time, index size and disk, RAM against size.
6. Mixed load: read latency with and without a writer.
7. Quality probes: hit@1/hit@10/MRR against size (labelled synthetic).

**Comparison table** against the previous completed run of the same preset: key metrics with the change; more than 20% worse is
flagged as a regression, 10-20% worse as slower, anything else "within normal run-to-run variation" **(my call; the thresholds
get calibrated after a few real runs)**.

**Run details:** SMOS version, run time, preset, seed, CPU model and count, memory, container limits, FAISS and Python versions,
embedding model name, and any warnings (client-limited steps, skipped phases).

### 6.1 PDF export (decided with the Governor: print to PDF)

- The report page has an **Export PDF** button. It opens the browser's print dialog on a print-styled version of the report, and
  the Governor saves it as PDF. No new dependency; the charts in the PDF are the same inline SVG as on the page.
- A print stylesheet (in the report template, `@media print`) hides the admin navigation, buttons and the "show data" toggles'
  chrome, expands every chart's data table, avoids page breaks inside a chart or tile, forces light colours with legible
  contrast, and adds a header with run id, date, SMOS version and preset on every page **(my call: these exact print details)**.
- The button only calls `window.print()` (a small inline script; the page already needs none otherwise, and works without it by
  using the browser's own print command). No server route is added for it.
- Verification: a test that the print rules exist and hide the navigation and controls; a manual check, recorded in
  `docs/benchmark-verification.md`, that a real report prints to a readable PDF (charts not cut, tables complete) in the browsers
  the Governor uses.

## 7. Storage and security

- Admin DB migration 5 adds `benchmark_runs(id, started_at, finished_at, status, preset, budget_ms, smos_version, hardware_json,
  result_json, error)`. `result_json` carries a `schema` version so old reports stay readable. The newest 50 runs are kept
  **(my call)**.
- Routes: `GET /admin/benchmark` (page and history), `POST /admin/benchmark/start`, `POST /admin/benchmark/cancel` (both CSRF,
  `admin_required`), `GET /admin/benchmark/status` (progress for the self-refresh), `GET /admin/benchmark/<id>` (report),
  `GET /admin/benchmark/<id>.json` (download). All admin-only.
- The runner's server accepts connections only on loopback. Its token and admin database are created inside the throwaway
  directory and deleted with it.
- The benchmark flag refuses `/api/v1` requests in one middleware place, and a test enumerates the OpenAPI routes to prove every
  one of them (except health) returns 503 while it is set.

### 7.1 Cleanup after itself (requirement from the Governor)

A run leaves nothing behind except its stored report row. Concretely:

- **Files:** the whole `bench-tmp/<run id>/` directory (throwaway store, FAISS and HNSW files, admin DB, progress and result
  files, logs, model caches if any) is deleted on every exit route: completed, failed, cancelled, runner crash. The result is
  read into the admin DB *before* deletion; the directory is deleted even if storing the report fails.
- **Processes:** the runner, its server child and any load-client workers are ended (the whole process tree), and the service
  confirms they are gone before it reports the run finished.
- **State:** the benchmark flag is cleared, so `/api/v1` serves again; the in-memory progress is reset.
- **Memory:** because the runner is a separate process, its embedding model, indexes and caches are freed by process exit; the
  live service never loaded any of it.
- **Verified, not assumed:** after cleanup the service checks that the directory is gone and no child process remains, and
  records "cleanup verified" (or a visible warning naming what is left) in the report's run details.
- **After a crash or restart:** at service startup, stale `bench-tmp` directories are removed and `running` rows become
  `interrupted` (5.1 step 6). Directories are only ever removed under `bench-tmp/` inside the data directory, by exact path
  built from the run id, never by a pattern over the data directory **(my call: a guard so a bad id can never delete real
  data)**.
- **Tests:** cleanup after each exit route (success, failure, cancel, runner killed), stale-directory removal at startup, the
  path guard refusing anything outside `bench-tmp/`, and "no child process left". The guard tests are mutation-checked.

### 7.2 Start only from the admin panel

- The only code path that starts a run is `BenchmarkService.start`, called by `POST /admin/benchmark/start` after
  `admin_required` and the CSRF check. There is no `/api/v1` route for it (so a token, including a read/write token, cannot
  start one), no GET that starts anything, and no timer.
- The runner is launched only by the service. `python -m smos.bench` refuses to run unless it receives a one-time launch secret:
  the service creates a random secret per run, passes it to the child in an environment variable (never on the command line),
  and writes only its SHA-256 into the run directory. The runner exits with code 2 and the message "benchmarks are started from
  the admin page" when the secret is missing or wrong, or when its working directory is not a run directory under `bench-tmp/`
  **(my call: this mechanism)**.
- Honest limit: someone with shell access to the host can always run Python code; the guard stops the command line, the API and
  accidents, not a determined operator. The Governor's requirement is about the product's entry points.
- Tests: no OpenAPI route mentions benchmarks; a bearer token cannot reach `/admin/benchmark/start` (redirected to login); GET on
  the start URL is 405; the runner CLI refuses without the secret and with a wrong one; the service passes the secret and never
  logs it or puts it on the command line.

## 8. Failure modes to design for

- Runner crashes or is killed: status `failed` with the last progress message; flag cleared; directory removed.
- Disk fills: the runner stops, the error says disk; cleanup as above.
- Live service restarts mid-run: see 5.1 step 6.
- Cancel pressed during ingest or while a server child is starting: the whole process tree ends within seconds.
- Admin presses Start twice or from two browser tabs: the second is refused (one run at a time, enforced server-side).
- Update check or *Apply update* during a run: refused with a clear message; the benchmark page says why.
- Very slow machine: the wall-time cap fails the run with a message pointing at a smaller preset.

## 9. Verification

- Unit tests (offline, as the rest of the suite): corpus determinism and shape; percentile and throughput calculations; knee
  detection; the verdict rules (each branch); projection; comparison and regression flags; chart functions; migration 5 on a fresh
  and on an existing admin DB; the maintenance gate over every OpenAPI route; mutual refusal with the updater; start/cancel/
  status routes with a fake runner command; report page rendering from a stored fixture; startup cleanup of stale directories and
  `running` rows.
- Guard tests are mutation-checked: each is made to fail once on purpose.
- A real end-to-end run of a tiny preset against the real embedding model on the dev PC, then Quick and Standard on the dev PC,
  recorded in `docs/benchmark-verification.md` like earlier projects. The Governor then runs it on the Debian host; the measured
  numbers replace the pessimistic latency figures in `ARCHITECTURE.md` section 4 and the HNSW assessment gets a measured update.
- Codex review package afterwards, as for earlier projects.

## 10. Left to the builder

The runner's internal module layout; the exact HTTP client library for the load phases; how per-stage timing is obtained without
changing production behaviour (prefer calling the same functions directly and timing them; if a hook in production code is
unavoidable it must cost nothing when unused); the progress file format; exact page copy; chart styling within the dashboard's
existing look; precise preset sizes after the first real runs.

# SMOS Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline, no subagents).

**Goal:** The admin dashboard of the spec: tiles and line charts for load, latency, growth and hardware, top calls, per-token activity and recent events, offline, with history in `admin.db`.

**Architecture:** admin migration 3 adds a `samples` table; a background `Sampler` writes one hardware/store sample per minute; `dashboard_data.py` aggregates `usage_log` and `samples` with short single-statement queries; `charts.py` draws inline SVG; `/admin/dashboard` renders it.

**Tech Stack:** FastAPI/Jinja2 (existing), SQLite window functions, `psutil` (fallback only).

**Spec:** `docs/superpowers/specs/2026-10-28-dashboard-design.md`

## Global Constraints

- No external assets or scripts: the page and charts work offline. No company-specific names anywhere in files or commits.
- Never hold `app.state.admin_lock` across more than one SQL statement in dashboard code or the sampler.
- A failed sample is logged and skipped, never raised into the app.
- Baseline before Task 1: 444 passing tests; use `rtk proxy pytest` if "No tests collected".

## Review Focus

- Empty database (no usage, no samples): the page renders with zeros and empty charts, no exception.
- A range with a single data point, or all-equal values (flat line, zero range on the y-axis): chart still renders.
- `/sys/fs/cgroup` missing, v1 layout, `memory.max` = `max`, `cpu.max` = `max 100000`: sensible limits, no crash.
- Invalid `?range=` value: falls back to 24h.
- Hostile text (`<script>` in a token label or path) shown on the page: escaped.
- Sampler and API requests running together: no lock contention errors; samples pruned by retention.
- The path recorded in the usage log for parameterised routes is the route template, so "top calls" does not fragment by id.

---

### Task 1: Samples table, hardware reader, sampler

**Files:** Modify `smos/admin/db.py` (migration 3), `smos/memory/vector_store.py` (`stats()`, `file_sizes()`), `smos/admin/usage.py` (`prune_samples_batch`); Create `smos/admin/metrics.py`; Test `tests/test_metrics.py`.

**Interfaces:** Produces `VectorStore.stats() -> {"documents": int, "segments": int, "memories": int}`; `VectorStore.file_sizes() -> {"store_db": int, "index": int}`; `metrics.read_hardware(cgroup_root: Path = Path("/sys/fs/cgroup"), previous: Optional[dict] = None, now: Optional[float] = None) -> dict` with keys `cpu_percent` (of allocated cores, None on first read), `cpu_cores`, `mem_used`, `mem_limit`, `cpu_state` (opaque, pass back as `previous`); `metrics.Sampler(app, interval: float)` with `sample_once() -> bool`, `start()`, `stop()`; `usage.prune_samples_batch(conn, retention_days, limit) -> int`.

- [ ] Write failing tests: cgroup v2 fixture files (`memory.current`, `memory.max`, `cpu.max`, `cpu.stat`) give used/limit/cores; `max` limits fall back to host totals; v1 fixture layout works; a missing root falls back to `psutil`; CPU percent computed from two readings; `stats()` counts documents (not placeholders) and segments; `Sampler.sample_once()` inserts one row with all columns and returns True, returns False (no raise) when the store raises; migration 3 creates `samples` on a v2 admin DB and keeps existing rows; `prune_samples_batch` deletes only old rows within the limit.
- [ ] Run (FAIL), implement, run (PASS), full suite, commit `feat: sampler and samples table`.

### Task 2: SVG line charts

**Files:** Create `smos/admin/charts.py`; Test `tests/test_charts.py`.

**Interfaces:** Produces `downsample(points: list[tuple[float, float]], max_points: int = 240) -> list[tuple[float, float]]` (bucket average, order kept); `Series(name: str, points: list[tuple[float, float]], css_class: str)`; `line_chart(series: list[Series], *, title: str, y_unit: str = "", y_max: Optional[float] = None, reference: Optional[tuple[str, float]] = None, width: int = 640, height: int = 200) -> str` returning an inline `<svg>` string (role="img", aria-label, a `<title>`, polylines, 4 gridlines, x labels as local-independent UTC `HH:MM`/`MM-DD`, legend, optional dashed reference line); `format_bytes(n) -> str`.

- [ ] Write failing tests: downsample keeps ≤ max points and the mean; an empty series list gives an SVG with a "no data" text; one point and flat values render; reference line appears with its label; text is escaped (`<script>` in a name); output contains no `http://` or `https://` other than the SVG namespace; `format_bytes` cases.
- [ ] Implement, run, commit `feat: inline SVG line charts`.

### Task 3: Aggregation queries

**Files:** Create `smos/admin/dashboard_data.py`; Modify `smos/api/app.py` (record the route template as the usage path when the router matched one); Test `tests/test_dashboard_data.py`.

**Interfaces:** Produces `RANGES = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}`; `parse_range(value: Optional[str]) -> str` (default `"24h"`); `bucket_seconds(range_s: int) -> int` (smallest of 60, 300, 900, 3600, 10800, 21600 that gives ≤ 240 buckets); functions taking `(conn, lock, since_epoch, ...)` and taking the lock only around each single statement: `tiles(...)`, `load_series(...)` (ok and failed calls per bucket, gaps filled with 0), `latency_series(...)` (average and 95th percentile per bucket via a window function), `top_calls(..., limit=8)`, `token_activity(...)`, `recent_events(..., limit=15)` (errors, 401/403 labelled as auth events, and successful writes), `hardware_series(...)` and `growth_series(...)` from `samples`.

- [ ] Write failing tests with synthetic `usage_log`/`samples` rows at explicit timestamps: bucket counts and gap filling, p95 value, error classification, events ordering and kinds, top calls grouped by route template, ranges and invalid range fallback, empty database returns zeros/empty lists, the middleware stores `/api/v1/nodes/{node_id}/text` (not the concrete id) in the usage log.
- [ ] Implement, run, commit `feat: dashboard aggregation queries`.

### Task 4: The dashboard page

**Files:** Create `smos/admin/templates/dashboard.html`; Modify `smos/admin/pages.py`, `smos/admin/templates/base.html` (nav: Dashboard, Access, Usage), `smos/api/app.py` (start the sampler, stop it in `close`, extend the background prune with samples retention `SMOS_METRICS_RETENTION_DAYS` default 30; `create_app(..., start_sampler: bool = True)`), `pyproject.toml` (`psutil`); Test `tests/test_dashboard_page.py`.

**Interfaces:** `GET /admin/dashboard?range=1h|24h|7d|30d` (admin only; anonymous redirects to the login); login and `/admin` redirect to it; page contains the nine panels of the spec, `<meta http-equiv="refresh" content="60">`, range links, no external URLs, escaped content.

- [ ] Write failing tests: anonymous redirect; login lands on the dashboard; tiles reflect a document ingested through the API and calls made; each range link works and an invalid range falls back; empty database renders; a token named `<script>alert(1)</script>` is escaped; no `src=`/`href=` to another host; nav shows the three links; update banner shows the version.
- [ ] Implement, run the full suite, commit `feat: admin dashboard page`.

### Task 5: Verify for real and document

- [ ] Run the service locally, generate traffic with curl (ingest, queries, one 403, one 401), open `/admin/dashboard` with curl and check the numbers against the usage page; confirm a sample row per minute.
- [ ] Rebuild the image with Docker Desktop, run it, confirm the hardware panel shows the container's memory limit/cores from cgroup files and the page works offline (`--network none`).
- [ ] Update `docs/API.md` (admin pages + new variables), `docs/INSTALL.md` (dashboard section), `docs/HANDOFF.md`, add `docs/dashboard-verification.md`; commit `docs: dashboard verification`.

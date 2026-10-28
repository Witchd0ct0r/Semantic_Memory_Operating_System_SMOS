# SMOS Standalone — Project 3: Dashboard and metrics (design)

Date: 2026-10-28. Status: draft for Governor approval. Follows Projects 1 (core) and 2 (Docker).

## Intent

Give the admin one page that shows, at a glance, whether SMOS is healthy, how much it holds, how
much it is used and by whom, and what resources it consumes over time. It replaces guessing from
logs and lets a trial be judged with evidence.

## Decisions from the Governor (mockup session)

- Layout **A**: one overview page with everything on it (not tabs).
- **Line charts** for everything that changes over time.
- Content: items ingested, database size, tool load over time, most-used API calls, hardware
  resource allocation over time (CPU, memory, disk), plus an update banner and a recent-events list.
- Behind the admin login. The per-token usage view built in Project 1 stays and is linked from here.

## Goals

1. `/admin/dashboard` (the landing page after login) shows the panels below for a selectable time
   range: last hour, 24 hours, 7 days, 30 days.
2. Works offline and without JavaScript libraries: charts are drawn by SMOS itself as inline SVG.
3. History survives restarts (stored in `admin.db`) and cannot grow without bound.
4. Metrics collection costs next to nothing and never slows or breaks API calls.

## Non-goals

The update check and update button (Project 4; the banner slot only shows the running version and a
"update checking arrives with the updater" note until then); alerting or e-mail; per-user permissions
beyond the single admin; exporting to Prometheus/Grafana; anything that needs internet.

## Panels (top to bottom)

1. **Update banner slot:** running version; filled in by Project 4.
2. **Tiles:** documents, segments (stored items), database size (metadata DB + search index + admin DB),
   API calls in the selected range, error rate, average latency.
3. **API load:** calls per minute (or hour for long ranges), split into successful and failed.
4. **Latency:** average and 95th percentile over time.
5. **Growth:** documents and segments over time; database size over time.
6. **Hardware:** CPU %, memory used versus the container's limit, disk used versus the volume size, each
   over time, with the allocated limit drawn as a reference line. Tiles beside them state the
   allocation (CPU cores, memory limit, disk size).
7. **Most-used API calls:** top endpoints by count, with error counts and average latency.
8. **Per-token activity:** calls, errors and last seen per token (links to the existing usage view).
9. **Recent events:** the latest ingests (writes), errors (status 400 or higher) and authentication
   failures, newest first.

## Design (builder's calls; reject any)

- **Sampler:** a background thread started with the app takes one sample every 60 seconds and writes a
  row to a new `samples` table in `admin.db` (admin migration 3): timestamp, CPU percent, CPU cores
  allocated, memory used, memory limit, disk used, disk total, store database bytes, search index bytes,
  admin database bytes, document count, segment count. A failed sample is skipped and logged, never raised.
- **Hardware source:** the container's own view. Memory and CPU come from the cgroup files
  (`/sys/fs/cgroup`, v2 first, then v1) so the limit and usage are the container's, not the host's;
  fall back to `psutil` when the files are absent (development). Disk comes from `shutil.disk_usage('/data')`.
  New dependency: `psutil`.
- **API load, latency, top calls, per-token, events:** computed from `usage_log` (already recorded per
  call), with SQL aggregation per time bucket. No extra writes on the request path.
- **Retention:** `samples` kept 30 days (about 43,000 rows) and pruned together with the usage log
  (`SMOS_USAGE_RETENTION_DAYS` still governs the usage log; `SMOS_METRICS_RETENTION_DAYS`, default 30,
  governs samples), in the existing batched background prune.
- **Charts:** a small server-side SVG line-chart renderer (`smos/admin/charts.py`): axes, a few gridlines,
  time labels, a reference line, a legend, light and dark colours through CSS variables, and a text
  table fallback for screen readers. Series are reduced to at most 240 points per chart by bucket averaging.
- **Page:** server-rendered Jinja template extending the admin base; the time range is a query parameter
  (`?range=24h`); a `<meta http-equiv="refresh">` reloads it every 60 seconds (no JavaScript needed).
- **Navigation:** the admin header gets **Dashboard** (first), **Access**, **Usage**; login lands on
  the dashboard.

## Anti-goals (failure even if tests pass)

- The sampler or an aggregation query able to block or slow API requests (the admin lock is shared
  with token authentication: hold it only for short single statements, never for a whole aggregation
  loop).
- A chart that needs a CDN, fonts or scripts from the internet.
- Numbers that disagree with the usage log or the database (counts must come from the same sources
  the rest of SMOS uses).
- Unbounded growth of the admin database.

## Left to the builder

Exact SQL, bucket sizes per range, colours and spacing, wording of tiles, how the cgroup files are
parsed, and the page's responsive behaviour.

## Risks

- cgroup file layouts differ (v1/v2, Docker Desktop versus native Linux); mitigated by fallbacks and
  tests with fixture files.
- Counting documents and segments on a very large store on every sample: use cheap `COUNT(*)` queries on
  indexed columns and cache the value between samples; measure before optimizing.

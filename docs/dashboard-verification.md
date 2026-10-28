# Project 3 (dashboard) — verification record

Date: 2026-10-28. Spec: `docs/superpowers/specs/2026-10-28-dashboard-design.md`.
Plan: `docs/superpowers/plans/2026-10-28-dashboard.md`.

## What was built

- Admin migration 3: `samples` table; `Sampler` (one row per minute: CPU of allocated cores, memory used and
  limit, disk used and volume size, database and index sizes, document and segment counts).
- `read_hardware`: the container's own numbers from the cgroup files (v2, then v1), host numbers through
  `psutil` when there are none (development).
- `dashboard_data`: aggregation queries over `usage_log` and `samples`; each takes the shared admin lock for a
  single statement only (a test fails if it is ever held deeper).
- `charts`: inline SVG line charts (no scripts, fonts or network).
- `/admin/dashboard` (landing page after login): tiles, API load, latency (average and 95th percentile),
  growth, hardware against allocation, most-used calls, per-token activity, recent events; ranges 1h, 24h,
  7d, 30d; refreshes every minute.
- The usage log now stores the matched route template (`/api/v1/nodes/{node_id}/text`), so top calls do not
  fragment by id.
- Retention: `SMOS_METRICS_RETENTION_DAYS` (default 30) for samples, batched with the usage-log pruning.

## Checks

| Check | Result |
|---|---|
| Test suite | 511 passed (stable over repeated runs) |
| Real service, local run, traffic from curl (3 ingests, 4 queries, 1 call without a token) | tiles: 3 documents, 3 segments, 8 API calls, 12.5% error rate (1 of 8), matching the traffic; 7 charts; top calls, token activity and events listed correctly |
| Page opened in a browser | renders cleanly; found and fixed: a single data point drew nothing, overlapping x labels, long timestamps |
| Container with limits (`--memory 1536m --cpus 1.5`) | dashboard shows 1.5 CPU cores and a 1.5 GB memory limit, equal to the container's cgroup files (`cpu.max 150000 100000`, `memory.max 1610612736`); 4 samples stored at a 5 s interval |
| Offline | the real page contains 0 external URLs and no `<script>` tag; all assets are inline |
| Image rebuild with `psutil` | 458 s, exit 0 |

## Not verified here

Long-running behaviour (days of samples, pruning of a large backlog beyond the unit tests), a very large store
(document/segment counts use `COUNT(*)` once per minute), and the Debian machine (the Governor's step:
`git pull`, rebuild, open the dashboard).

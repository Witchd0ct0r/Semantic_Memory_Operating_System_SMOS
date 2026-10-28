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

## Independent review (Codex) and follow-up

Review: `docs/superpowers/review/2026-10-28-third-review.md` (range `77165cc..75099fe`): four Important findings,
no Critical; the Docker packaging, the second-review fixes and the dashboard's SQL, escaping, access control
and migration were judged sound. All four were reproduced with a failing test and fixed:

| Finding | Fix | Tests |
|---|---|---|
| Unmatched unauthenticated paths could grow and fragment the usage log | paths that match no route are stored as `(unmatched)`; requests without a valid token get a per-minute logging budget (`SMOS_UNAUTH_LOG_PER_MIN`, extra ones are counted, not stored); a hard row cap (`SMOS_USAGE_MAX_ROWS`) enforced at start, daily and every `SMOS_USAGE_PRUNE_EVERY_ROWS` rows | `tests/test_usage_bounds.py` |
| Dashboard aggregation blocked the event loop | the handler is a plain `def`, so FastAPI runs it in a worker thread | `tests/test_dashboard_page.py` |
| Sampler counts held the vector-store lock across table scans | counts use a separate read-only (WAL) connection and never touch `_lock`; indexes on `memories(type)` (store migration 2) and `graph_nodes(type)` | `tests/test_metrics.py`, `tests/test_migrations.py` |
| A zero cgroup CPU quota made every later hardware sample fail; negative memory figures were accepted | quota, memory and `cpu.max` values are validated: zero, negative, `max` or garbage mean "no usable limit" and fall back to the host | `tests/test_metrics.py` |

Codex's own test run stalled at 42%. Reproduced: with no network and no Hugging Face cache the suite hung on the
first embedding (the tests loaded the model by name). The tests are now hermetic: `tests/conftest.py` points them at
the bundled model folder with offline flags (the same path the container uses), and `faulthandler_timeout = 300`
makes a stuck test print its stack instead of stalling silently. Verified: with an unreachable Hugging Face endpoint
and an empty cache the same tests pass in 10 seconds. Suite: 527 passed.

## Not verified here

Long-running behaviour (days of samples, pruning of a large backlog beyond the unit tests), a very large store
(document/segment counts use `COUNT(*)` once per minute), and the Debian machine (the Governor's step:
`git pull`, rebuild, open the dashboard).

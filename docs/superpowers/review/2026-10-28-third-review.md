# SMOS Dashboard, Docker, and Follow-up Review

**Date:** 2026-10-28  
**Reviewed range:** `77165cc..75099fe`  
**Review basis:** third review package, dashboard and Docker design specs, verification records, implementation, and tests.

## Findings

### Important — Unmatched unauthenticated API paths can grow and fragment the usage log

The usage middleware records every `/api/v1` request other than health, including 404s with no matched route; those rows store the caller-controlled raw path, and there is no request-rate limit or row-count bound. Age-based pruning does not prevent an attacker from continuously adding fresh rows, while unique paths also fragment the dashboard's top-call aggregation.

**Reproduction:** Send repeated unauthenticated `GET /api/v1/probe-<unique-value>` requests. Each 404 is inserted with a null token and a distinct raw path; continue sending them faster than the 90-day age retention expires and `usage_log` grows without a volume bound.

**Location:** `smos/api/app.py:114-133`; dashboard grouping at `smos/admin/dashboard_data.py:95-104`.

### Important — Dashboard aggregation blocks the async server event loop

The `async` dashboard route calls `build_dashboard` inline, and that function performs synchronous SQLite queries and storage-stat work before returning. Those operations therefore block the event loop that also serves API requests; a large usage history or slow store-stat query can make unrelated API requests wait for the whole dashboard render.

**Reproduction:** Populate a large `usage_log`/`samples` history, request `/admin/dashboard`, and concurrently call an API endpoint. While the dashboard's synchronous queries run, the event loop cannot begin processing the API request.

**Location:** `smos/admin/pages.py:198-205`; synchronous aggregation begins at `smos/admin/dashboard_view.py:22-42`.

### Important — Sampler counts hold the vector-store lock across full table scans

`VectorStore.stats()` describes its counts as cheap, but performs three unindexed `COUNT(*)` scans while holding the same `_lock` used by store operations. The periodic sampler calls it on every sample, so a large store can block concurrent reads and writes while the counts scan the tables.

**Reproduction:** Load a large number of memories, then let a sampler interval run while issuing API queries or ingests. During `stats()`, those operations wait for the store lock until all three counts finish.

**Location:** `smos/memory/vector_store.py:431-442`; sampler call at `smos/admin/metrics.py:116-120`.

### Important — A zero cgroup CPU quota makes every later hardware sample fail

The cgroup-v2 reader accepts a quota of zero and reports `cpu_cores=0`; on the next reading with a previous CPU counter, `_cpu_percent` divides by `elapsed_us * cores`, raises `ZeroDivisionError`, and the sampler skips the sample. Since the failed sample does not advance its previous state, subsequent samples continue failing while the zero quota remains. Negative memory values are also accepted as real figures instead of rejected or normalized.

**Reproduction:** Set cgroup-v2 `cpu.max` to `0 100000` and provide increasing `usage_usec` values in `cpu.stat`. The first read seeds state; the second raises in `_cpu_percent`, and each sampler attempt thereafter is skipped. A negative `memory.current` or numeric `memory.max` is returned and persisted as a negative dashboard value.

**Location:** `smos/admin/metrics.py:64-73`; division at `smos/admin/metrics.py:38-44`; sample failure handling at `smos/admin/metrics.py:103-120`.

## Checks that looked sound

- **Section 3 — Dashboard:** SQL inputs are bound or selected from fixed ranges; query buckets are numeric constants. Jinja autoescaping covers table values, and the SVG builder escapes titles, labels, and CSS class values before the template inserts its output. The charts handle empty, single-point, flat, and all-zero series without invalid SVG coordinates; negative values are clamped to the chart baseline. The p95 query selects the nearest-rank 95th percentile, status `>=400` is consistently counted as an error, the event query includes errors and writes, and the token/activity joins and UTC range comparisons were consistent with the stored data model. Dashboard access remains behind the admin-session dependency, and I found no token hashes or secrets in dashboard output. Migration 3 creates the samples table without dropping existing data. The three findings above cover the blocking and unbounded-growth gaps; I found no shared-lock deadlock or multi-statement admin-lock hold in the reviewed paths.
- **Section 4 — Docker:** `.env` is excluded from the build context and git; credentials are supplied at runtime and are not copied into image layers. The optional CA bundle is public trust data, excluded from git as intended. The container runs as uid 10001, writes application data under `/data`, has a health check, and does not request privileged mode or the Docker socket. A fresh named volume receives the image directory ownership; a pre-existing volume owned by another uid fails with a permissions error rather than silently losing data. The bundled embedding model is loaded by path with offline environment flags, and model validation occurs before the data directory is opened. Plain HTTP and `SMOS_PUBLIC_URL`/Host-header behavior match the documented deployment decisions; I found no open redirect in the invitation-link flow. The floating base tag and unpinned dependencies make rebuilds non-reproducible, but the package explicitly asks to flag this only when judged important; I did not find a new concrete runtime compromise in the reviewed build configuration. The example admin password is a placeholder and the install instructions tell operators to replace it before first start.
- **Section 5 — Part A fixes:** Reference-counted document locks account for holders and waiters and remove entries after the final release; I found no double-release or delete/recreate race. Pruning deletes bounded batches in a background thread and serializes connection access with the shared admin lock; the sampler uses the same lock around its database write. The router-level scope pin classifies registered routes independently of OpenAPI visibility. The remaining store-lock contention noted above comes from statistics scans, not from the document-lock fix.

## Verification

`python -m pytest tests/ -q` was started as requested. The run reached 42%, then stopped producing output well beyond the package's expected runtime while the Python process remained alive; I interrupted it and could not obtain a final pass/fail result. The package's recorded 511-pass result is historical evidence, not a result confirmed by this run.

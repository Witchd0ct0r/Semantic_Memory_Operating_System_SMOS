# Review package 5: 0.3.0 release, rebuild-fingerprint fix, store batching, graph migration

Fifth review round. Earlier ones: `e903ff7..82c1a5c`, `82c1a5c..77165cc`, `77165cc..75099fe`, and review 4 on the
self-update (`ede8ab5..8412f2e`, findings fixed in `c042d4c`, resolution table in `docs/update-verification.md` section 4).
This package covers `c042d4c..HEAD` (`git diff c042d4c..HEAD`, 10 commits; HEAD is tagged `v0.3.1` plus later doc-only
commits if any). Find real defects (data loss, wrong results, security, regressions, gaps). Style nits are not wanted.
Report only; do not edit code.

## 1. Scope

Production code changed (about +165 / -135 lines outside tests):

- **A. Rebuild-fingerprint defect fix** (`5ed9e7a`). `smos/update/manifest.py::REBUILD_PATHS` (unchanged) lists
  `pyproject.toml`, `Dockerfile`, `compose.yaml`, `models/`, `smos/launcher.py`; the first and third used to carry the
  version number, so every release differed from the installed image. Now `pyproject.toml` has
  `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "smos.__version__"}`, `compose.yaml` has
  `image: smos:${SMOS_IMAGE_TAG:-latest}`, the Dockerfile header comment lost its version. Tests:
  `tests/test_versioning.py` (no version literal in any rebuild file; two releases differing only in `__init__.py` give an
  empty `differing()`), `tests/test_packaging.py`. Docs: `docs/RELEASING.md` step 1.
- **B. Batched reads** (`8b619f1`, `4b0532e`). `VectorStore._query_internal` read each FAISS hit with its own SELECT; now
  `_rows_by_row_id` (one `WHERE row_id IN (...)` per chunk of 500). New `VectorStore.get_many(uuids)` (chunked, de-duplicated,
  missing ids absent) replaces N `get_by_uuid` calls in `context_builder.build_context`, `exact_retrieval._fetch_ranked_candidates`,
  `exact_retrieval` reconstruct, `requirement_context` candidate hydration. `get_by_uuid` is now `get_many([uuid])`.
  `_record_from_row` is shared. Tests: `tests/test_query_batching.py` (counts SELECTs through `set_trace_callback`),
  `tests/test_hybrid_retrieval.py` (mock now provides `get_many`).
- **C. Graph tables under the migration runner** (`da56880`). `GraphStore` no longer creates tables; store migration 3
  (`smos/storage/migrations.py::_graph_tables`) does: the two legacy CHECK rebuilds (moved verbatim, minus the
  `commit()` calls the runner forbids), then `CREATE TABLE IF NOT EXISTS` for nodes, edges and the type index. Tests use
  `tests/helpers.py::migrated_graph`. New test adopts an existing 0.3.x graph.
- **D. Release** `v0.3.1` (`__version__` 0.3.1; tag and GitHub Release created, notes say "needs a rebuilt image").
- Docs only: `docs/hnsw-assessment.md`, `docs/runbooks/update-drill.md`, `ARCHITECTURE.md` section 5 item 2, handoffs.

## 2. Run it

```bash
pip install -e ".[dev]"
rtk proxy python -m pytest tests/ -q     # expect 750 passed, about 2 min (a bare `pytest` may say "No tests collected")
```

Use `python`, not `python3`. The suite is hermetic and offline.

## 3. What to attack, hardest first

1. **Migration 3 on real data directories.** Existing installs (Debian, 0.3.0) are at store schema version 2 with graph
   tables created by `GraphStore`. Does version 3 leave graph rows untouched in every shape: current schema, 5.1-era
   `graph_nodes` (has `'document'`, no `'section'`), pre-Phase-5 `graph_edges` with `CHECK(edge_type`, both together, tables
   absent? The runner wraps all pending migrations in one transaction and takes a `.bak-v2` copy first: is `ALTER TABLE ...
   RENAME` + `CREATE` + `INSERT ... SELECT` + `DROP` inside `BEGIN IMMEDIATE` correct for SQLite (legacy-alter-table and
   foreign-key behaviour, indexes dropped with the renamed table, `idx_graph_nodes_type` recreated)? Anything that previously
   happened *every* open (the old `_init_schema` ran on each start) and no longer does, e.g. a database whose
   `schema_version` is already 3 but whose graph tables were dropped?
2. **`get_many` / `_rows_by_row_id` correctness.** Order preservation (callers iterate the original id list; BM25-only
   hits are fetched separately), duplicates, ids absent from SQLite (index/DB divergence), chunk boundary at 500 with the
   `>999` host-parameter limit of older SQLite builds, rows deleted between the FAISS search and the SELECT (both run under
   `_lock`). `build_context` previously skipped a falsy record; `requirement_context` wraps `{**fetched, "score": None}`:
   unchanged? Is any caller still doing the N-query pattern?
3. **Lock behaviour.** `get_many` takes `VectorStore._lock`; `_query_internal` calls `_rows_by_row_id` while already holding
   it (it must not re-acquire: `Lock` is not re-entrant). Confirm there is no path that calls `get_many` while holding
   `_lock` (deadlock) — `lifecycle.py` and `agent_primitives.py` still use `get_by_uuid`, which now goes through `get_many`.
4. **Single-sourced version.** `dynamic` version via `attr`: does `pip install --no-deps .` in the Dockerfile (where only
   `smos/` and `README.md` are copied before the install) still resolve it, and does anything else read
   `pyproject.toml["project"]["version"]` (scripts, docs, the launcher, `python -m smos.update.manifest`)? Is
   `SMOS_IMAGE_TAG:-latest` a problem for the rollback story (an image rebuilt under the same tag replaces the old one) or
   for `docker compose` defaults? Does a Windows CRLF working copy of the three rebuild files hash like git (`.gitattributes`
   `eol=lf`, `test_repo_rebuild_files_match_git_head`)?
5. **Tests that give false comfort.** `test_release_that_only_bumps_the_version_needs_no_rebuild` copies the repo's own
   files; would it fail if someone re-introduced a version into `compose.yaml`? (`test_rebuild_files_carry_no_version_number`
   is meant to catch that.) Does `test_query_batching` fail if the per-hit SELECT came back?
6. Anything in the 0.3.0 release commit (`fb808b6`: version bump, publish workflow removal) that now looks wrong.

## 4. Known limits and honest gaps

- **Still not exercised inside the production image:** an in-place update, rollback and the SIGTERM/PID-1 path. A drill is
  prepared in `docs/runbooks/update-drill.md` (throwaway repository, good release then deliberately failing one); the
  Governor runs it on Debian after rebuilding to 0.3.1.
- **Not done on purpose:** `embed()` outside `VectorStore._lock`, incremental BM25, tier-driven eviction (not needed before
  any HNSW work; `docs/hnsw-assessment.md` recommends against HNSW for now). `do_ingest_document_schema` remains
  non-atomic across lock acquisitions by design: it is chunked and checkpointed so a failure resumes (`ingest_checkpoints`);
  readers can see a half-ingested document while it runs. Making it atomic would need staged writes and a swap and is not
  proposed.
- The HNSW probe used random vectors for timings only; no recall figure is claimed.
- A stale `smos` editable install on the dev PC reports 0.2.0 in `pip list` (metadata only); tests import from the repo.

## 5. How to report

For each finding: severity (Critical / Important / Minor), file and line, the failing scenario with concrete input, and a
suggested fix in one or two sentences. Write the report to `docs/superpowers/review/2026-10-29-5-review.md`.

# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone. Overwrite stale sections rather than appending a
history log; git history is the log.

---

## Where things stand (2026-10-28, session 7)

**Direction change: SMOS is becoming a standalone, self-hosted product** the Governor will
install in Docker inside their company for trial runs. A local agent and third-party parsers
(including ones the Governor writes) talk to it over the internal network. The built-in
generative AI and the MCP server are **removed**; summaries are supplied by callers.

The standalone effort is five projects, one design at a time, in this order:

| # | Project | Status |
|---|---|---|
| 1 | **Core**: no built-in AI, caller-supplied summaries, HTTP API + OpenAPI, bearer-token auth with invitation links, per-token usage, versioned migrations, minimal admin pages | **implemented** (see below) |
| 2 | Docker packaging: image, data volume, healthcheck, install guide | next |
| 3 | Full dashboard: metrics history (items, DB size, API load, hardware over time, top calls), line charts, layout A (single overview page) chosen in brainstorming | queued |
| 4 | Self-update from remote `main` on request: version check, backup, migrate, rollback; update button + checker on the dashboard | queued |
| 5 | Documentation: install guide, user guide, parser-author guide, agent-oriented API guide (grows with each step) | queued |

Design: `docs/superpowers/specs/2026-10-28-standalone-core-design.md`.
Plan: `docs/superpowers/plans/2026-10-28-standalone-core.md`.

### Project 1 (Core) — what was built

- **Summaries:** `summaries(node_id, summary, source)` table; `VectorStore.set_summary /
  get_summaries / delete_summary`; `IngestionPayload.summary` + `summary_source`;
  `PUT /api/v1/nodes/{id}/summary` for any node. Retrieval returns exact text plus the stored
  summary (`null` when none).
- **AI removed:** `smos/llm/`, `openai`, Ollama, the file/repo tools, the MCP server
  (`smos/server.py`) and the installer (`smos/install/`). `compress` / `reflect` now store text
  the caller wrote and link it to its sources. Retrieval is `build_context()` returning
  `RetrievedContext {results, sources, confidence, mode}`.
- **API:** `smos/api` (FastAPI), `/api/v1/...`, OpenAPI at `/api/v1/openapi.json`. See
  `docs/API.md`. Every route except `/health` needs a bearer token.
- **Auth:** tokens are random, shown once, stored hashed. Issued only through one-time
  invitation links (`/claim/<secret>`): the admin creates an invitation, sends the link, the
  user presses *Create token*; the admin list shows who claimed it (time, IP, user agent).
  Admin login (scrypt), CSRF on admin forms, failed login/claim throttling.
- **Usage log:** one row per `/api/v1` call (token id, path, status, latency, sizes; never
  bodies) in `admin.db`; per-token view at `/admin/usage`.
- **Migrations:** `smos/storage/migrations.py`; forward-only, one transaction, backup file
  before upgrading; `SchemaTooNewError` if the data is newer than the code. Store DB and
  `admin.db` each have their own list. GraphStore still creates its own tables (move under
  migrations when it next changes).
- **Packaging:** distribution `smos` 0.2.0, console script `smos-server`; admin templates are
  package data.
- **Removed with it:** the `smos-doc-sync` skill and its test, `docs/runbooks/scripts/ingest_code.py`,
  and benchmark scripts that depended on the removed code.

### Things to know

- **No stored data exists** to migrate; migration 1 is a plain baseline.
- Development is on `main`; the future updater follows the remote `main`. `origin` is still
  far behind local `main` (nothing has been pushed).
- The Swagger page (`/api/v1/docs`) loads assets from a CDN; offline installs read the
  OpenAPI JSON directly.
- The Governor's user-scope MCP registration is now stale: `claude mcp remove smos --scope user`
  (not run by the assistant). See `docs/runbooks/environment.md`.
- The test suite runs in ~30 s now that no test calls a model (it was ~6 min).

### Parked work (not lost)

The earlier 5.5c idea (domain scope queries, bundle evidence for requirement review, a
`guidance` doctype for ASPICE/process documents) is parked. Agreed principle: **no new parallel
functions**; extend existing endpoints (parameters and output fields) and write an agent
playbook. Gaps found: results are top-k only and don't separate "reached by explicit link" from
"found by similarity only"; `normative` is hard-wired to `doctype:legal|standard`; parent
membership isn't used for domain membership.

## Prior phases (details in git history)

Phases 1-3 (BM25 hybrid retrieval, memory graph, agent primitives), Phase 5.1-5.5b (document
ingestion contract, segmentation, exact retrieval, markdown/legal adapters), Track C
(per-document citation namespacing), requirements ingestion and `requirement_context`.

## Standing technical debt

- `embed()` is called while holding `VectorStore._lock`.
- N individual SQLite SELECTs per query result (no `WHERE ... IN` batching).
- Tier labels are computed but don't drive eviction or search.
- `BM25Index.update()/remove()` rebuild fully per call.
- `GraphStore` has no corpus backfill; its DDL is outside the migration runner.
- `GraphStore.traverse()` is undirected: a `contains` query from a section returns its parent too;
  consumers filter by node type.
- `do_ingest_document_schema()` is not atomic across its separate lock acquisitions.
- The admin DB shares one SQLite connection behind a lock; fine for a trial, revisit under load.

## Test commands

```bash
pytest tests/ -q
```

If a bare `pytest` reports "No tests collected", use `rtk proxy pytest tests/ -q` or
`python -m pytest tests/ -q`. `bench_*.py` / `scale_*.py` are standalone scripts.

# SMOS — Current State & Handoff

**Update this file at the end of every session** (what shipped, what's next) so a fresh
session can orient from this file alone. Overwrite stale sections rather than appending a
history log; git history is the log.

---

## Where things stand (2026-10-29, session 8, evening)

> **Latest handoff (read first):** `docs/handoffs/2026-10-29-1700-next-dashboard-page-and-benchmarking.md` (next session: a new dashboard
> page and automated benchmarking; brainstorm first). Previous: `docs/handoffs/2026-10-29-1530-project-5-documentation-done.md`.
> Projects 1-5 are implemented. v0.3.1 is the latest real release; Debian runs 0.3.2 (the update drill's overlay), so the next real
> release must be 0.3.3 or higher. Project 5 added `docs/guide/`, a tested example parser (`docs/examples/`) and guard tests; it
> recorded seven behaviors as technical debt (see the handoff). Codex review 6 is resolved
> (3 findings, `docs/guide-verification.md` section 6). Open: the two Debian checks
> (`docs/guide-verification.md` section 4), choosing the next project from the debt list.

**Direction change: SMOS is becoming a standalone, self-hosted product** the Governor will
install in Docker inside their company for trial runs. A local agent and third-party parsers
(including ones the Governor writes) talk to it over the internal network. The built-in
generative AI and the MCP server are **removed**; summaries are supplied by callers.

The standalone effort is five projects, one design at a time, in this order:

| # | Project | Status |
|---|---|---|
| 1 | **Core**: no built-in AI, caller-supplied summaries, HTTP API + OpenAPI, bearer-token auth with invitation links, per-token usage, versioned migrations, minimal admin pages | **implemented and verified** (`docs/standalone-core-verification.md`) |
| 2 | Docker packaging: image, data volume, healthcheck, install guide | **done and running on the Governor's Debian machine** (`docs/docker-verification.md`, `docs/INSTALL.md`) |
| 3 | Full dashboard: metrics history (items, DB size, API load, hardware over time, top calls), line charts, layout A (single overview page) chosen in brainstorming | **implemented** (spec `2026-10-28-dashboard-design.md`, plan `2026-10-28-dashboard.md`); verification record `docs/dashboard-verification.md` |
| 4 | Self-update from **GitHub Releases** (not commits): version check, snapshot, rollback of code and data, update button + checker on the dashboard | **implemented and Codex-reviewed (review 4: 1 Critical + 3 Important + 1 Minor, all fixed with tests)**, 743 tests; real end-to-end run 33/33 (`docs/update-verification.md`); NOT yet run inside the Docker image; package (spec `2026-10-28-self-update-design.md`, plan `2026-10-28-self-update.md`, `docs/RELEASING.md`) |
| 5 | Documentation: guide for parser authors, agents and reporters (`docs/guide/`), tested example parser, guard tests | **implemented** (spec `2026-10-29-documentation-design.md`, plan `2026-10-29-documentation.md`, record `docs/guide-verification.md`); Codex review 6 resolved |

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
  `admin.db` each have their own list. The graph tables are store migration 3 since 0.3.1 (were created by GraphStore; moved under
  the migration runner).
- **Packaging:** distribution `smos` 0.2.0, console script `smos-server`; admin templates are
  package data.
- **Removed with it:** the `smos-doc-sync` skill and its test, `docs/runbooks/scripts/ingest_code.py`,
  and benchmark scripts that depended on the removed code.

### Things to know

- Verified against the real running service (invitation flow, summaries, revoke) and the legal bench
  (unchanged). The real run caught one defect (document summary not visible on segment hits) which
  is fixed: results now carry `document_summary`.
- Codex reviewed `e903ff7..82c1a5c` (`docs/superpowers/review/`): 1 Critical + 4 Important, all fixed with tests
  (access-log secret redaction, per-document ingest lock, section summaries on hits, section-summary cleanup
  on re-ingest, usage-log retention). Details in `docs/standalone-core-verification.md` §5. 399 tests pass.
  A reverse proxy in front of SMOS needs its own `/claim/*` log handling (Project 2 install guide).
- **Token scopes (added after the Codex review):** tokens are `read` or `rw`, set per invitation
  (form defaults to read). Write endpoints live on `routes.write` behind `require_write` (403 for read
  tokens); `tests/test_api_scopes.py` pins the exact write-operation list, so a new endpoint fails the test
  until classified. Admin DB migration 2 (`scope` column, existing rows `rw`). 423 tests pass.
- Second Codex review (`82c1a5c..77165cc`): 3 Important, all fixed (ref-counted per-document locks, batched
  background usage pruning, router-level route-classification guard). 430 tests pass. Details:
  `docs/standalone-core-verification.md` §7.
- **Project 2 done:** `Dockerfile`, `compose.yaml`, `.env.example`, bundled model in `models/`, `docs/INSTALL.md`.
  Company network blocks Hugging Face and the PyTorch CPU index, so the image uses PyPI's GPU PyTorch (large) and
  the model is loaded from `SMOS_EMBEDDING_MODEL_DIR`. Open item: Swagger UI assets still come from a CDN.
- Debian install works (ingest + query verified from a client PC). Learned there: the network re-signs HTTPS (extra CA
  bundle in `certs/`, git-ignored), port 8765 was blocked while 8080 worked (`SMOS_HOST_PORT`), some browsers force
  HTTPS. Never put the employer's names/hosts in the repo (memory `feedback-no-company-data-in-repo`).
- Project 3 done (dashboard, `docs/dashboard-verification.md`); third Codex review: 4 Important, all fixed (usage-log bounds, off-loop dashboard, lock-free counts, cgroup validation) and the suite is now hermetic/offline (527 tests). Project 4 (self-update from releases) followed; see the table above and `docs/update-verification.md`.
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
- Tier labels are computed but don't drive eviction or search.
- `BM25Index.update()/remove()` rebuild fully per call.
- `GraphStore` has no corpus backfill (its tables are created by store migration 3 since 0.3.1).
- `GraphStore.traverse()` is undirected: a `contains` query from a section returns its parent too;
  consumers filter by node type.
- `do_ingest_document_schema()` is chunked and checkpointed (resumable), not atomic: readers can see a half-ingested document while it runs. By design.
- The admin DB shares one SQLite connection behind a lock; fine for a trial, revisit under load.

## Test commands

```bash
pytest tests/ -q
```

If a bare `pytest` reports "No tests collected", use `rtk proxy pytest tests/ -q` or
`python -m pytest tests/ -q`. `bench_*.py` / `scale_*.py` are standalone scripts.

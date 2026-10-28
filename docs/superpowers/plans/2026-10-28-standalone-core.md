# SMOS Standalone Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the Governor chose inline execution; do not dispatch subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn SMOS into an HTTP-API service with no built-in generative AI, caller-supplied summaries, bearer-token auth with per-token usage, versioned storage migrations, and a minimal admin page.

**Architecture:** Existing retrieval, graph, segmenter and adapter code is kept. Everything that called the local LLM is replaced by caller-supplied text, stored in a new `summaries` table keyed by node id. A FastAPI app (`smos/api`) wraps the existing functions one-to-one under `/api/v1`; a separate `admin.db` holds accounts, tokens and the usage log. A small migration runner versions both databases.

**Tech Stack:** Python >= 3.10, FastAPI, uvicorn, Jinja2, python-multipart, itsdangerous (sessions), httpx (tests), existing FAISS / sentence-transformers / SQLite. Password hashing with stdlib `hashlib.scrypt`; no new crypto dependency.

**Spec:** `docs/superpowers/specs/2026-10-28-standalone-core-design.md`

## Global Constraints

- Branch: work on `main` of this repo (Governor decision). One commit per task, end each with the commit trailer given in the session attribution rules.
- No code path may import `openai`, call Ollama, or generate text. `smos/llm/` and the `openai` / `mcp` dependencies are removed.
- Every `/api/v1` route except `GET /api/v1/health` requires `Authorization: Bearer <token>`. The OpenAPI JSON and docs page require a bearer token or an admin session.
- Tokens: 256-bit random (`secrets.token_urlsafe(32)`), shown once, stored only as SHA-256 hash. Admin passwords stored with scrypt.
- Missing summary is `null` in responses, never fabricated or synthesized.
- Migrations are forward-only, numbered, run at startup before serving, with a file-copy backup of the DB taken first when the DB already exists at an older version.
- Tokens are issued only through single-use invitation links claimed by the user; no admin route creates or displays a token plaintext. Invitation secrets are 256-bit random, stored only as SHA-256 hash, default expiry 72 hours (`SMOS_INVITE_TTL_HOURS`). Client IP is the direct peer unless `SMOS_TRUST_PROXY=1` (then first `X-Forwarded-For` entry).
- Usage log retention default 90 days (`SMOS_USAGE_RETENTION_DAYS`).
- No stored data exists, so migration 1 is a plain baseline; no 0.1.x upgrade path.
- Environment: use `pytest` / `python`, never `python3`. If pytest reports "No tests collected", retry with `rtk proxy pytest`.
- Baseline before Task 1: record the passing-test count from `pytest tests/ -q --tb=short` (expected 381).

## Review Focus

- Request with no token, a malformed `Authorization` header, a revoked token, or a token in the query string: always 401, never data.
- `/api/v1/openapi.json` and `/api/v1/docs` reached without credentials: 401, not served.
- A token's plaintext appearing anywhere after creation (DB, logs, usage rows, list page, admin pages at any time): must not.
- An invitation link used twice, used concurrently by two clients, used after expiry or revocation, or guessed with a wrong secret: exactly one claim can succeed; every other case gets the same neutral "link not valid" page and creates nothing.
- Repeated wrong-secret probing of `/claim/...` from one client: throttled.
- Retrieval over items with no summary: `summary: null`, request still succeeds.
- Opening a data directory whose `schema_version` is newer than the code: refuse to start with a clear error rather than run.
- A failed migration: DB restored from the backup, startup aborts.
- Usage rows for rejected (401) requests: recorded without a token id, so probing is visible; the request body is never stored.
- Admin login brute force and a missing bootstrap env on first start: login refused / startup fails with a clear message, no default password.

---

### Task 1: Summaries table and retrieval shape

**Files:**
- Modify: `smos/memory/vector_store.py` (`_init_db`, add methods), `smos/memory/schemas.py`
- Test: `tests/test_summaries.py`

**Interfaces:**
- Produces: `VectorStore.set_summary(node_id: str, summary: str, source: str = "") -> None`; `VectorStore.get_summaries(node_ids: list[str]) -> dict[str, dict]` mapping id to `{"summary": str, "source": str}`; `VectorStore.delete` also removes the item's summary row.
- Produces (schemas): `RetrievedItem(id: str, content: str, summary: Optional[str], summary_source: Optional[str], score: Optional[float], metadata: dict)` and `RetrievedContext(results: list[RetrievedItem], sources: list[str], confidence: float, mode: Literal["extractive","uncertain"])`. `CompressedContext` is removed in Task 2.

- [ ] **Step 1: Write failing tests** in `tests/test_summaries.py`: `test_set_and_get_summary_roundtrip` (set for a stored memory id, `get_summaries([id])[id] == {"summary": "s", "source": "parser-1"}`), `test_get_summaries_missing_ids_absent`, `test_set_summary_replaces_existing`, `test_delete_memory_removes_summary`, `test_summary_works_for_graph_node_ids` (set on a document node id that is not a memory uuid).
- [ ] **Step 2: Run** `pytest tests/test_summaries.py -v`; expect FAIL (no `set_summary`).
- [ ] **Step 3: Implement** the `summaries(node_id TEXT PRIMARY KEY, summary TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)` table in `_init_db` (this DDL moves into migration 1 in Task 3), the two methods under `self._lock`, and the delete hook. Add `RetrievedItem` / `RetrievedContext` to `schemas.py`.
- [ ] **Step 4: Run** the test file; expect PASS.
- [ ] **Step 5: Commit** `feat: store caller-supplied summaries per node`.

### Task 2: Remove the built-in AI

**Files:**
- Delete: `smos/llm/` (all), `smos/tools/file_tools.py`, `tests/test_summarizer.py`, `tests/test_file_tools.py`, `tests/bench_summarizer.py`, `tests/bench_compression.py`, `tests/scale_s4_compression.py`, `tests/bench_mcp_tools.py`
- Modify: `smos/compression/context_builder.py`, `smos/memory/recall_engine.py`, `smos/tools/semantic_tools.py`, `smos/tools/agent_primitives.py`, `smos/tools/ingest_tools.py`, `smos/memory/schemas.py`, and every test or bench listed by `grep -rln "build_compressed_context\|CompressedContext\|compress_memories\|summarize_text\|reflect_on_memories" tests smos`
- Test: update `tests/test_semantic_tools.py`, `tests/test_hybrid_retrieval.py`, `tests/test_recall_engine.py`, `tests/test_agent_primitives.py`, `tests/test_graph_expansion.py`, `tests/test_exact_term_recall.py`, `tests/test_legal_adapter.py`, `tests/test_reference_and_domain_tagging_e2e.py`

**Interfaces:**
- Consumes: Task 1 schemas and `VectorStore.get_summaries`.
- Produces:
  - `build_context(query: str, k: int, store: VectorStore, tags: Optional[list[str]] = None) -> RetrievedContext` (renamed from `build_compressed_context`; same candidate/RRF/graph-expansion logic; builds `RetrievedItem`s with summaries attached via one `get_summaries` call; `confidence` keeps the `1 - avg_distance/2` formula clamped to 0..1; `mode` is `"extractive"` when results exist else `"uncertain"`).
  - `semantic_query(query, k, store, tags=None) -> dict` returns `RetrievedContext.model_dump()`.
  - `recall(...)` hybrid/repo/temporal strategies return the same shape; `chain` and `exact` unchanged.
  - `compress(text: str, source_ids: list[str], store) -> str` and `reflect(text: str, source_ids: list[str], store) -> str`: validate `text` with `_validate_text`, require every source id to exist (return `""` otherwise), store `MemoryObject(type="compressed"|"reflection", content=text, source_ids=...)`, add `generated_from` edges as today. Return the new id.
  - `snapshot` primitive removed; `semantic_store`/`semantic_write` unchanged (callers pass a summary through Task 5's API, not here).
  - `ingest_tools.py` keeps only `delete_document_segments` and `do_ingest_document_schema` plus helpers they use; the repo/file ingestion, `_arch_summary_via_llm`, `do_bulk_read`, `do_semantic_snapshot_repo` and `get_ingested_paths` / `mark_files_ingested_batch` users are deleted (the `ingested_files` table goes with them).

- [ ] **Step 1: Update tests first** to the new contract: assertions on `result["summary"]` become assertions on `result["results"]` (ids, content, `summary is None`), `mode == "extractive"`; add `test_query_returns_stored_summary` and `test_query_summary_null_when_absent` to `tests/test_semantic_tools.py`; `test_compress_stores_caller_text_and_links_sources`, `test_compress_unknown_source_returns_empty`, `test_reflect_stores_caller_text` in `tests/test_agent_primitives.py`.
- [ ] **Step 2: Run** `pytest tests/ -q --tb=short`; expect the updated tests FAIL.
- [ ] **Step 3: Implement** the interface changes above and delete the listed files. Delete `ingested_files` DDL and its two `VectorStore` methods.
- [ ] **Step 4: Confirm no model code remains:** `grep -rniE "openai|ollama|get_llm_client|smos\.llm" smos tests` returns nothing (except this plan's wording is not in those dirs).
- [ ] **Step 5: Run** `pytest tests/ -q --tb=short`; expect all pass (count will be lower than baseline by the deleted tests; record it).
- [ ] **Step 6: Commit** `feat: remove built-in AI, retrieval returns exact text plus stored summaries`.

### Task 3: Migration runner and baseline

**Files:**
- Create: `smos/storage/__init__.py`, `smos/storage/migrations.py`
- Modify: `smos/memory/vector_store.py` (`_init_db` delegates to the runner)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]` (version, name, apply); `run_migrations(conn: sqlite3.Connection, migrations: list[Migration], db_path: Optional[Path] = None) -> int` returning the resulting version; `SchemaTooNewError(RuntimeError)`; `STORE_MIGRATIONS: list[Migration]` (memory store, version 1 = baseline).
- Behavior: a `schema_version(version INTEGER NOT NULL)` table; applies pending migrations in order, each in a transaction; before the first pending migration on an already-versioned DB with `db_path` given, copies the file to `<db_path>.bak-v<old>`; on failure restores that copy and re-raises; a stored version greater than the highest known raises `SchemaTooNewError`.

- [ ] **Step 1: Write failing tests** in `tests/test_migrations.py`: `test_fresh_db_applies_all_in_order`, `test_rerun_is_noop`, `test_failed_migration_restores_backup_and_raises` (a second migration that raises; first migration's data from an earlier run still present), `test_newer_schema_refused`, `test_backup_file_created_before_upgrade`, `test_store_baseline_creates_tables` (a `VectorStore` on an empty dir has `memories`, `verbatim`, `ingest_quarantine`, `ingest_checkpoints`, `summaries`, and `schema_version` = 1).
- [ ] **Step 2: Run** `pytest tests/test_migrations.py -v`; expect FAIL.
- [ ] **Step 3: Implement** the runner. Migration 1 holds the full current `memories` DDL (all columns inline, no try/except ALTERs), `verbatim`, `ingest_quarantine`, `ingest_checkpoints`, `summaries`. `GraphStore` keeps creating its own tables (my call; graph DDL moves under migrations when it next changes). `VectorStore._init_db` opens the connection, sets the pragmas, calls `run_migrations(conn, STORE_MIGRATIONS, self._db_file)`.
- [ ] **Step 4: Run** `pytest tests/ -q --tb=short`; expect all pass.
- [ ] **Step 5: Commit** `feat: versioned storage migrations with backup and rollback`.

### Task 4: Admin database, accounts, tokens, usage

**Files:**
- Create: `smos/admin/__init__.py`, `smos/admin/db.py`, `smos/admin/accounts.py`, `smos/admin/tokens.py`, `smos/admin/usage.py`
- Test: `tests/test_admin_tokens.py`, `tests/test_admin_accounts.py`, `tests/test_admin_usage.py`

**Interfaces:**
- Consumes: `run_migrations` from Task 3.
- Produces:
  - `open_admin_db(data_dir: Path) -> sqlite3.Connection` (file `admin.db`, own `ADMIN_MIGRATIONS` version 1: tables `admins`, `tokens`, `invitations`, `usage_log`, `settings`).
  - `invitations.create_invitation(conn, label: str, admin_username: str, ttl_hours: int = 72) -> tuple[int, str]` (id, secret; the secret is shown to the admin once, inside the link, and only its hash is stored); `invitations.claim(conn, secret: str, client_ip: str, user_agent: str) -> Optional[tuple[int, str]]` (atomically marks the invitation claimed with a single `UPDATE ... WHERE claimed_at IS NULL AND revoked = 0 AND expires_at > now` and, only if it changed one row, creates the token named after the label and records `claimed_at`, `claimed_ip`, `claimed_user_agent`, `token_id`; returns (token id, plaintext) or `None`); `invitations.is_claimable(conn, secret: str) -> bool` (read-only, for rendering the claim page); `invitations.list_invitations(conn) -> list[dict]` (label, created_by, created_at, expires_at, status `pending|claimed|expired|revoked`, and for claimed ones claimed_at / ip / user agent / token id; never the secret or hash); `invitations.revoke_invitation(conn, invitation_id: int) -> bool` (only while pending).
  - `tokens.create_token` stays as an internal helper used by `claim` and tests; no route calls it directly.
  - `accounts.bootstrap_admin(conn, username: str, password: str) -> bool` (creates the first admin only if none exists), `accounts.verify_login(conn, username: str, password: str) -> bool`, `accounts.get_session_secret(conn) -> str` (random, generated once, stored in `settings`).
  - `tokens.create_token(conn, name: str) -> tuple[int, str]` (id, plaintext shown once), `tokens.list_tokens(conn) -> list[dict]` (id, name, created_at, last_used_at, revoked; never the hash), `tokens.revoke_token(conn, token_id: int) -> bool`, `tokens.authenticate(conn, presented: str) -> Optional[int]` (token id if valid and not revoked, updates `last_used_at`).
  - `usage.record(conn, *, token_id: Optional[int], method: str, path: str, status: int, latency_ms: float, req_bytes: int, resp_bytes: int) -> None`; `usage.per_token_summary(conn, since_days: int = 30) -> list[dict]` (token id, name, calls, errors, last_seen, bytes); `usage.recent(conn, token_id: Optional[int], limit: int = 100) -> list[dict]`; `usage.prune(conn, retention_days: int) -> int`.

- [ ] **Step 1: Write failing tests:** invitations in `tests/test_admin_invitations.py`: `test_claim_creates_token_once` (second claim returns `None`, only one token exists), `test_claim_records_ip_and_user_agent`, `test_concurrent_claims_only_one_wins` (two threads, same secret), `test_expired_invitation_not_claimable`, `test_revoked_invitation_not_claimable`, `test_wrong_secret_none`, `test_secret_and_token_plaintext_not_stored`, `test_claimed_token_inherits_label_and_authenticates`, `test_list_invitations_statuses_and_no_secret_fields`, `test_revoke_only_while_pending`. Then the rest: `test_token_plaintext_not_stored` (scan every column of every table for the plaintext), `test_authenticate_valid_then_revoked`, `test_authenticate_unknown_and_empty_none`, `test_list_tokens_has_no_secret_fields`, `test_bootstrap_only_once`, `test_verify_login_wrong_password_false`, `test_password_not_stored_plain`, `test_usage_record_and_per_token_summary`, `test_usage_unauthenticated_row_has_null_token`, `test_prune_removes_only_old_rows`.
- [ ] **Step 2: Run** the four test files (add `tests/test_admin_invitations.py` and `smos/admin/invitations.py` to this task's Files); expect FAIL.
- [ ] **Step 3: Implement** with `hashlib.sha256` for tokens and invitation secrets, `hashlib.scrypt` with per-password random salt for passwords, `hmac.compare_digest` for comparisons.
- [ ] **Step 4: Run** the three files; expect PASS.
- [ ] **Step 5: Commit** `feat: admin accounts, hashed API tokens, usage log`.

### Task 5: HTTP API

**Files:**
- Create: `smos/api/__init__.py`, `smos/api/app.py`, `smos/api/deps.py`, `smos/api/models.py`, `smos/api/routes.py`, `smos/api/main.py`
- Modify: `smos/ingest/contract.py` (summary fields), `smos/tools/ingest_tools.py` (persist payload summary)
- Test: `tests/test_api_auth.py`, `tests/test_api_routes.py`, `tests/test_api_openapi.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: `create_app(data_dir: Path) -> FastAPI` (builds `VectorStore`, `LifecycleManager`, admin DB; stores them on `app.state`; default docs/redoc/openapi routes disabled); `main.run() -> None` (reads `SMOS_DATA_DIR` default `/data`, `SMOS_HOST` default `0.0.0.0`, `SMOS_PORT` default `8765`, runs uvicorn); `deps.require_token` (FastAPI dependency; 401 with `WWW-Authenticate: Bearer` on missing/invalid/revoked; sets `request.state.token_id`).
- Payload change: `IngestionPayload.summary: Optional[str] = None` and `summary_source: Optional[str] = None`; `do_ingest_document_schema` stores them via `set_summary(doc_id, ...)` after a successful ingest (replaced on re-sync; cleared if absent on re-sync).
- Routes (all under `/api/v1`, JSON in/out, pydantic models in `models.py`; each handler is a thin wrapper over the existing function named in brackets):
  - `GET /health` (unauthenticated, returns `{"status": "ok", "version": str}` only)
  - `POST /memories` {type, content, tags?, summary?, summary_source?} -> {id} [`semantic_write`; learn / remember are this route with type `semantic` / `episodic`]
  - `DELETE /memories/{id}` [`forget`]
  - `POST /memories/derived` {kind: "compressed"|"reflection", text, source_ids} -> {id} [`compress` / `reflect`]
  - `POST /query` {query, k=5, tags?} -> `RetrievedContext` [`semantic_query`]
  - `POST /recall` {query, strategy, k, since, until, seed_id, component} [`recall`]
  - `POST /graph/query` {term, max_hops=2} [`graph_query`]
  - `POST /links` {id_a, id_b, edge_type} [`link`]
  - `GET /nodes/{node_id}/text` [`reconstruct`]
  - `PUT /nodes/{node_id}/summary` {summary, source?} -> {ok} [`set_summary`; 404 if the id is neither a memory nor a graph node]
  - `POST /documents` body = `IngestionPayload` [`do_ingest_document_schema`; schema violations -> 422 from FastAPI]
  - `POST /requirement-context` (same fields as the old tool, `domain_tags` / `normative_tags` as lists, `declared_links` as a list of objects) [`requirement_context`]
  - `POST /verbatim` {content, label?} -> {key}; `GET /verbatim/{key}` [`store_verbatim` / `retrieve_verbatim`]
  - `GET /openapi.json` and `GET /docs`: served at `/api/v1/openapi.json` and `/api/v1/docs`, protected by `require_token_or_admin_session`.
- A middleware records one `usage.record` row per request under `/api/v1` including 401s (token_id null), measuring latency and sizes; bodies are never stored.

- [ ] **Step 1: Write failing auth tests** in `tests/test_api_auth.py` using FastAPI `TestClient` on a temp data dir with a token from `create_token`: `test_no_header_401`, `test_malformed_header_401`, `test_wrong_scheme_401`, `test_revoked_token_401`, `test_token_in_query_string_rejected`, `test_health_open`, `test_openapi_and_docs_require_credentials`, `test_every_route_in_schema_is_protected` (iterate `app.routes` under `/api/v1`, call each with no token, expect 401 except health), `test_401_is_logged_without_token_id`, `test_usage_row_has_no_body`.
- [ ] **Step 2: Write failing route tests** in `tests/test_api_routes.py`: `test_post_memory_then_query_returns_it_with_summary`, `test_query_without_summary_has_null`, `test_put_node_summary_on_document_node`, `test_ingest_document_with_summary_roundtrip`, `test_documents_bad_payload_422`, `test_derived_memory_links_sources`, `test_derived_unknown_source_400`, `test_recall_exact_strategy`, `test_graph_query_and_link`, `test_requirement_context_route`, `test_verbatim_roundtrip`. `tests/test_api_openapi.py`: `test_openapi_has_summary_field_and_operation_descriptions` (every operation has a non-empty `summary` and `description`).
- [ ] **Step 3: Run** both; expect FAIL.
- [ ] **Step 4: Implement** `create_app`, dependencies, middleware, models and routes. Give every route a docstring-based description written for an agent: what it does, when to use it, the fields that matter (these feed the OpenAPI spec). Errors use FastAPI's `{"detail": ...}`.
- [ ] **Step 5: Run** `pytest tests/ -q --tb=short`; expect all pass.
- [ ] **Step 6: Commit** `feat: authenticated HTTP API with OpenAPI`.

### Task 6: Minimal admin pages

**Files:**
- Create: `smos/admin/pages.py`, `smos/admin/templates/{base,login,tokens,usage}.html`
- Modify: `smos/api/app.py` (mount pages and `SessionMiddleware`, startup bootstrap)
- Test: `tests/test_admin_pages.py`

**Interfaces:**
- Consumes: Task 4 account/token/usage functions, Task 5 `create_app`.
- Produces: routes `GET/POST /admin/login`, `POST /admin/logout`, `GET /admin/tokens` (token list, each row showing label, created time, and for invitation-issued tokens the claim time, IP and user agent, plus revoke), `POST /admin/invitations` (admin enters a label; the response page shows the full one-time link `<base>/claim/<secret>` once, with a copy button), `GET /admin/invitations` (pending / claimed / expired / revoked list), `POST /admin/invitations/{id}/revoke`, `POST /admin/tokens/{id}/revoke`, `GET /admin/usage` (per-token table, `?token=<id>` shows that token's recent calls). Session cookie via `SessionMiddleware(secret_key=get_session_secret(conn), same_site="strict", https_only` from `SMOS_COOKIE_SECURE`). Startup: `SMOS_ADMIN_USER` and `SMOS_ADMIN_PASSWORD` bootstrap the first admin; if no admin exists and either is unset, `create_app` raises `RuntimeError` with a message naming both variables. State-changing admin POSTs require a per-session CSRF token field. Public, no login: `GET /claim/{secret}` (page with a "Create token" button when `is_claimable`, otherwise the neutral "link not valid" page) and `POST /claim/{secret}` (calls `invitations.claim` with the client IP and `User-Agent`; on success renders the token once with a "save it now, it will not be shown again" notice and `Cache-Control: no-store`; otherwise the same neutral page with status 404). The claim route is excluded from admin-session checks and from the `/api/v1` usage log; failed claims are throttled per client host like logins.

- [ ] **Step 1: Write failing tests:** `test_pages_redirect_to_login_when_anonymous`, `test_login_wrong_password_rejected`, `test_full_invitation_flow` (admin logs in and creates an invitation, user without any session GETs and POSTs the link, token shown once, admin token list shows the claim with IP and user agent, token works on the API), `test_claim_link_second_use_shows_invalid_page`, `test_expired_and_revoked_link_show_same_invalid_page`, `test_claim_page_no_store_and_no_plaintext_in_admin_pages`, `test_invalid_claim_attempts_throttled`, `test_trust_proxy_uses_forwarded_ip_only_when_enabled`, `test_no_admin_route_creates_a_token_directly`, `test_revoked_token_stops_working`, `test_usage_page_lists_calls_per_token`, `test_post_without_csrf_rejected`, `test_startup_without_admin_env_fails_clearly`, `test_repeated_failed_logins_are_throttled` (after 5 failures in a minute from one client, further attempts get 429 even with the right password).
- [ ] **Step 2: Run** `pytest tests/test_admin_pages.py -v`; expect FAIL.
- [ ] **Step 3: Implement** pages with Jinja2 server-rendered HTML (no JS framework, no external assets so it works offline); in-memory login throttle keyed by client host.
- [ ] **Step 4: Run** `pytest tests/ -q --tb=short`; expect all pass.
- [ ] **Step 5: Commit** `feat: admin login, token management, per-token usage page`.

### Task 7: Retire MCP and clean up the repo

**Files:**
- Delete: `smos/server.py`, `smos/install/` (all), `tests/test_install_cli.py`, `tests/bench_*.py` and `tests/scale_*.py` that still import removed code after Task 2 and cannot be cheaply fixed (delete, list them in the commit message), `.claude/skills/smos-doc-sync/`, `tests/test_doc_sync.py`
- Modify: `pyproject.toml`, `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, `docs/API.md` (replace with a pointer to the generated OpenAPI plus the endpoint table from Task 5), `docs/HANDOFF.md`, `docs/runbooks/environment.md`, `.gitignore` (add `.superpowers/`)
- Test: `tests/test_packaging.py`

**Interfaces:**
- Produces: console script `smos-server = "smos.api.main:run"`; distribution renamed `smos` (my call), version `0.2.0`; description no longer mentions MCP or Claude Code; dependencies: remove `mcp[cli]`, `openai`; add `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `itsdangerous`; dev adds `httpx`.

- [ ] **Step 1: Write failing test** `tests/test_packaging.py`: `test_no_mcp_or_openai_imports_in_package` (walk `smos/**/*.py`, assert no `import mcp`, `openai`, `smos.llm`, `smos.install`), `test_pyproject_entry_point_and_deps` (parse `pyproject.toml`).
- [ ] **Step 2: Run** it; expect FAIL.
- [ ] **Step 3: Apply** the deletions and edits. In `CLAUDE.md` replace the "SMOS active" section and all MCP / SMOS-tool file-reading policy with a short "Standalone service" description pointing at the spec and plan; keep the workflow and test sections. In the handoff, state the new direction, the five-project order, what Project 1 changed, and that `smos-doc-sync` was removed because it relied on the MCP-era setup. In `environment.md`, remove the MCP registration section and note: the Governor's user-scope registration is removed with `claude mcp remove smos --scope user` (do not run it; tell the Governor).
- [ ] **Step 4: Run** `pip install -e .` then `pytest tests/ -q --tb=short`; expect all pass. `grep -rniE "mcp|tool_semantic|ollama" CLAUDE.md README.md ARCHITECTURE.md docs/runbooks docs/HANDOFF.md` should show only deliberate historical mentions in HANDOFF.
- [ ] **Step 5: Commit** `chore: retire MCP server, installer and AI dependencies; package as smos 0.2.0`.

### Task 8: Run it for real and measure retrieval

**Files:**
- Create: `docs/standalone-core-verification.md`
- Test: manual run plus `tests/bench_legal_regulations.py`

**Interfaces:** none new.

- [ ] **Step 1: Start the service:** `SMOS_DATA_DIR=<temp dir> SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD=<temp> smos-server`; stop it when finished.
- [ ] **Step 2: Drive the real flow with curl:** log in at `/admin/login`, create a token, `POST /api/v1/documents` with a small payload including `summary`, `POST /api/v1/query`, confirm the summary comes back, confirm a call without the token returns 401, revoke the token and confirm it stops working, view the usage page.
- [ ] **Step 3: Re-run** `python tests/bench_legal_regulations.py` (if it survived Task 7) and compare with the pre-change figures in `docs/HANDOFF.md` (graph-on cross-reference recall 100%); record both in `docs/standalone-core-verification.md` with the commands and outputs.
- [ ] **Step 4: Update `docs/HANDOFF.md`** with results and the Project 2 (Docker) next step; commit `docs: Project 1 verification and handoff`.

---

## Self-review notes

- Spec coverage: goals 1-7 map to Tasks 2, 5, 5, 4+5, 4+5, 3, 6; non-goals untouched; Governor decisions (main branch, MCP cleanup, no stored data) in Global Constraints and Task 7/3.
- The `ingested_files` table, repo ingestion and file tools are dropped (spec table); `learn` / `remember` collapse into `POST /memories` by type (my call, not in the spec table).
- Added 2026-10-28 by Governor request: one-time invitation links for token issuance (spec "Governor decisions" and "Invitation links"; plan Tasks 4 and 6, Global Constraints, Review Focus).
- Calls made here beyond the spec: `GET /health` is unauthenticated; the `/api/v1/docs` page also accepts an admin session; login throttle and CSRF on admin forms; graph tables stay outside migration 1.

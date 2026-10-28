# SMOS Standalone Core — Follow-up Review

**Date:** 2026-10-28  
**Reviewed range:** `82c1a5c..77165cc`  
**Review basis:** follow-up package, design, API documentation, verification record, implementation, and tests.

## Findings

### Important — Per-document locks accumulate for the lifetime of the process

`document_lock` stores one lock per distinct document ID and never removes it. Every unique external handle processed, including one whose payload is quarantined, therefore leaves an entry in the in-memory dictionary; a long-running service handling many distinct handles accumulates lock objects indefinitely.

**Reproduction:** Repeatedly submit document requests with distinct `external_handle` values. After each request completes, its lock remains in `VectorStore._doc_locks` for the life of the server.

**Location:** `smos/memory/vector_store.py:45` and `smos/memory/vector_store.py:371`.

### Important — Usage pruning can stall all API authentication while it deletes a large backlog

Pruning runs synchronously while holding the shared `admin_lock`, which token authentication and usage recording also need. If startup or the first request after a day’s interval must delete a large number of expired rows, API requests wait for that delete to finish.

**Reproduction:** Accumulate a large `usage_log` backlog beyond the configured retention, then restart the service or make an API request after the daily interval. The startup or triggering request performs the full delete while holding the lock used by API authentication.

**Location:** `smos/api/app.py:34`, `smos/api/app.py:73`, and `smos/api/app.py:98`.

### Important — The route-classification test can miss hidden API operations

`test_every_operation_is_classified` derives its operation set from `app.openapi()`, so a future mutating route added to the `read` router with `include_in_schema=False` is omitted from the pin and can be used by read-only tokens without the test noticing. No such hidden mutating route exists in the reviewed range.

**Reproduction:** Add a state-changing `/api/v1` route to `routes.read` with `include_in_schema=False`; the exact OpenAPI operation set remains unchanged, and the current read-token tests never call that route.

**Location:** `tests/test_api_scopes.py:37` and `smos/api/routes.py:30`.

## Checks that looked sound

- **Redaction coverage:** the custom Uvicorn access handler redacts the claim path, and the claim flow does not echo the secret in application responses, redirects, usage rows, or templates. I found no other in-application log or error path that includes it. Reverse-proxy logs remain outside this implementation, as documented.
- **Ingest locking:** concurrent ingests for the same document ID are serialized. The lock order does not introduce a deadlock with `VectorStore._lock` or the graph lock. The locks are process-local, consistent with the stated single-server-process deployment. The map-retention finding above remains.
- **Section summaries:** current containment creates at most one section level. `_section_id` follows incoming `contains` edges to the section; direct document children correctly have no section summary. Lookup work is bounded by the API’s `k` limit, though it performs graph queries per result.
- **Retention behavior:** pruning runs on startup and on the first API activity after the daily interval. An idle server can defer pruning until another request, as accepted in the package.
- **Read-only behavior:** current read endpoints only update the permitted recall counters in the memory store. Authentication timestamps and usage rows are written to the admin database as expected. I found no other content, graph, tier, or lifecycle mutation in the read paths reviewed.
- **Scope enforcement:** all current write routes use the write router and reject a read-only token with 403; invalid or missing tokens remain 401. OpenAPI/docs do not expose a write operation. The admin form validates scope server-side, claim inherits scope from the stored invitation, and there is no user route to change scope or mint an `rw` token directly.
- **Migration and defaults:** migration 2 adds the `rw` defaults for pre-existing rows as specified; new invitations default to `read`. The `create_token` helper defaults to `rw`, but no production route calls it; invitation claims use the invitation’s stored scope.
- **Classification pin:** the exact OpenAPI operation set catches new schema-visible operations and all current API operations are classified. Its hidden-route limitation is recorded above.

## Verification

`python -m pytest tests/ -q` — **423 passed** in 49.18 seconds. The suite downloaded the retained sentence-transformers embedding model; the removed local generative model is not involved.

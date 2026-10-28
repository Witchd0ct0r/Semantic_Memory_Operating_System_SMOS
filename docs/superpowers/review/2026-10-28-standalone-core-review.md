# SMOS Standalone Core — Code Review

**Date:** 2026-10-28  
**Reviewed range:** `e903ff7..82c1a5c`  
**Review basis:** implementation and the project review package, plan, and verification record. The test suite was not rerun for this review.

## Findings

### Critical — Invitation secrets appear in access logs

Opening or submitting `/claim/<secret>` places the invitation secret in the request path, which Uvicorn's default access log records. Anyone with access to those logs can claim an unclaimed invitation and mint an API token. This is the access-log issue already noted in the review package; its ability to grant a token makes the impact material.

**Reproduction:** Create an invitation, open its claim link, then inspect the Uvicorn access log for `/claim/<secret>`. Replay that secret before expiry to claim the invitation.

**Location:** `smos/admin/pages.py:183`, `smos/admin/pages.py:196`; Uvicorn starts in `smos/api/main.py:19`.

### Important — Concurrent re-ingests can leave duplicate or mixed document segments

The ingestion flow reads and updates checkpoints across multiple operations without a per-document lock. Two requests for the same `external_handle` can both observe no checkpoint, store independent segment batches, and overwrite the same checkpoint. Segments from both ingests remain searchable under the same document ID.

**Reproduction:** Send concurrent `POST /api/v1/documents` requests with the same `external_handle` and different document text. Search afterward; segments from both versions can be returned for the same logical document.

**Location:** `smos/tools/ingest_tools.py:71` (checkpoint read), `smos/tools/ingest_tools.py:143` (segment writes), and `smos/tools/ingest_tools.py:147` (checkpoint update).

### Important — Section summaries are not included in retrieval responses

The summary lookup for a retrieved segment checks only the segment ID and its document ID. A summary accepted for the containing section is therefore never returned with that segment.

**Reproduction:** Ingest a document that creates a section, attach a summary using `PUT /api/v1/nodes/{doc_id}:section:0/summary`, then query for a segment in that section. The result omits the section summary.

**Location:** `smos/compression/context_builder.py:55` and `smos/compression/context_builder.py:64`.

### Important — Re-ingest leaves summaries for deleted section nodes

Re-ingest removes section graph nodes but does not remove their summary rows. If a section ID is reused for changed content, the old summary remains associated with the new section ID.

**Reproduction:** Attach a summary to `doc_id:section:0`, then re-ingest the same document handle with changed section content. The graph node is removed and recreated, but the old row remains in the summaries table for that ID.

**Location:** `smos/tools/ingest_tools.py:40` and `smos/tools/ingest_tools.py:44`.

### Important — Usage-log retention is never applied

`usage.prune` is defined but has no runtime call site, and `SMOS_USAGE_RETENTION_DAYS` is not read. Usage rows older than the configured retention remain indefinitely, so the usage database grows without the designed nightly cleanup.

**Reproduction:** Keep the service running and recording API calls past the retention period; old `usage_log` rows remain because no startup or scheduled path invokes pruning.

**Location:** `smos/admin/usage.py:65`; there is no call site in `smos/`.

## Checks that looked sound

- **Auth coverage:** API routes inherit the bearer-token dependency; health is the only intentionally public API route. OpenAPI and docs have a separate token-or-admin-session dependency.
- **Admin surface:** state-changing admin POSTs check CSRF; Jinja templates escape displayed values; proxy IP selection follows `SMOS_TRUST_PROXY`; failed login and claim attempts are throttled per client host.
- **Migrations:** newer schema versions are refused, and pending migrations run under `BEGIN IMMEDIATE` with transaction rollback on failure.
- **Generated text and retrieval behavior:** no remaining model-call path was found. Hybrid retrieval, RRF ordering, graph expansion, and the confidence formula match the prior context builder’s behavior. Retrieval returns stored text and summaries rather than generated text.
- **Input checks:** query `k` is capped at 100, user-agent values are truncated to 300 characters, and invitation labels are rendered through autoescaping templates.

## Review limits

This was a source review; the test suite and real-service flow were not rerun. The review relies on the verification record for those results.

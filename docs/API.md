# SMOS API reference (0.2)

SMOS is a self-hosted store with exact-text retrieval. It never generates text: summaries
come from the caller and are returned beside the exact stored text.

The authoritative, always-current description is generated from the code:

- `GET /api/v1/openapi.json` — OpenAPI 3 document (every operation has an agent-oriented
  summary and description).
- `GET /api/v1/docs` — interactive page (Swagger UI; loads its assets from a CDN).

Both need a bearer token or an admin session. This file is the human summary.

## Authentication

Every `/api/v1` call except `GET /api/v1/health` needs `Authorization: Bearer <token>`.
Missing, malformed, unknown or revoked tokens get `401` with `WWW-Authenticate: Bearer`.
A token in the query string is ignored.

**Getting a token** (the admin never sees or sends it):

1. The user asks the admin for access.
2. The admin logs in at `/admin/login`, opens **Access**, enters a label for the user and
   creates an invitation. SMOS shows a one-time link `/claim/<secret>` once.
3. The admin sends the link to the user.
4. The user opens the link and presses **Create token**; the token appears once. They save it.
5. The admin's token list shows the token as created by the user (time, client IP, user agent).

Invitations are single-use, expire after 72 hours (`SMOS_INVITE_TTL_HOURS`) and can be
revoked while pending. Invalid, used, expired and revoked links all show the same page.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness (unauthenticated, no data) |
| POST | `/api/v1/memories` | Store text; optional `summary` + `summary_source` |
| DELETE | `/api/v1/memories/{id}` | Delete a memory, its edges and summary |
| POST | `/api/v1/memories/derived` | Store text you wrote (`compressed` / `reflection`) linked to `source_ids` |
| POST | `/api/v1/query` | Hybrid search → ranked exact text + stored summaries |
| POST | `/api/v1/recall` | Strategies: `hybrid`, `exact`, `temporal`, `chain`, `repo` |
| POST | `/api/v1/graph/query` | Walk the graph from an entity or node id |
| POST | `/api/v1/links` | Create a typed edge between two nodes |
| GET | `/api/v1/nodes/{node_id}/text` | Byte-exact text of a document or section |
| PUT | `/api/v1/nodes/{node_id}/summary` | Attach or replace a summary on any node |
| POST | `/api/v1/documents` | Ingest a structured document (the parser contract) |
| POST | `/api/v1/requirement-context` | Evidence for judging a requirement (no verdict) |
| POST | `/api/v1/verbatim` / GET `/api/v1/verbatim/{key}` | Lossless store / retrieve by key |

### Retrieval response

```json
{
  "results": [
    {"id": "…", "content": "exact stored text", "summary": null,
     "summary_source": null, "document_summary": "Caps radiated emissions.",
     "document_summary_source": "parser-1", "score": 0.83, "metadata": {"type": "segment", "doc_id": "…"}}
  ],
  "sources": ["…"],
  "confidence": 0.81,
  "mode": "extractive"
}
```

`summary` is the summary attached to that exact item; `document_summary` is the one attached to
its parent document (a segment hit shows both). Either is `null` when none was supplied. `mode` is `extractive`, or `uncertain` when
nothing matched.

### Ingestion contract (`POST /api/v1/documents`)

A third-party parser converts a source item to this JSON. Key fields:

- `external_handle` `{system, native_id, deep_link?, version?, source_updated_at?}` — the
  idempotent upsert key (`doc_id = sha1("system:native_id")[:16]`).
- `blocks[]` `{block_type: heading|paragraph|list|table, text, heading_level?, source_anchor?}`.
- `doc_type` (e.g. `requirement`, `legal`, `standard`), `domain_tags[]` (no commas),
  `metadata{}`, `source_links[] {target_native_id, edge_type, metadata{target_system?}}`.
- `summary`, `summary_source` — optional caller-written summary of the whole document.

Content that parses but breaks the rules (empty blocks, self-links, bad heading levels, …) is
quarantined: HTTP 200 with `{"status": "quarantined", "quarantine_key", "errors": [...]}`.
Schema violations return HTTP 422.

Typed edges the registered adapters understand include `references`, `depends_on`,
`parent_of`, `member_of`, `cites`, `contains`, `generated_from`, `related_to`, `evolves_into`.

### Errors

FastAPI's shape: `{"detail": "..."}`. `400` bad input (too-short text, unknown strategy or edge
type, unknown source id), `401` auth, `404` unknown id or key, `422` schema violation.

## Admin pages (not part of the API)

`/admin/login`, `/admin/tokens` (access: tokens, invitations), `/admin/usage` (per-token call
history), `/claim/<secret>` (public one-time token creation).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SMOS_DATA_DIR` | `/data` | Where `metadata.db`, `faiss.index` and `admin.db` live |
| `SMOS_HOST` / `SMOS_PORT` | `0.0.0.0` / `8765` | Bind address |
| `SMOS_ADMIN_USER` / `SMOS_ADMIN_PASSWORD` | — | Required on first start to create the admin |
| `SMOS_INVITE_TTL_HOURS` | `72` | Invitation lifetime |
| `SMOS_PUBLIC_URL` | request URL | Base URL put in invitation links |
| `SMOS_TRUST_PROXY` | off | `1` = use the first `X-Forwarded-For` entry as client IP |
| `SMOS_COOKIE_SECURE` | off | `1` = mark the admin cookie `Secure` (use behind HTTPS) |

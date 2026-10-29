# SMOS API reference

How to use it, with worked examples, is in the [guide](guide/README.md); this page is the endpoint summary.

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

**Token types.** Each token is either **read-only** or **read and write** (`rw`), chosen by the
admin when creating the invitation (the form defaults to read-only).

- A read-only token can only retrieve data: `POST /query`, `/recall`, `/graph/query`,
  `/requirement-context`, `GET /nodes/{id}/text`, `GET /verbatim/{key}`, and the OpenAPI/docs.
- Everything that changes the database needs a read/write token: `POST /memories`,
  `DELETE /memories/{id}`, `POST /memories/derived`, `POST /links`, `PUT /nodes/{id}/summary`,
  `POST /documents`, `POST /verbatim`. A read-only token gets `403 {"detail": "this token is
  read-only"}` (a missing or invalid token is still `401`).
- Searches update an internal recall counter (a usage statistic behind the hot/warm/cold tiers).
  That is the only state a read-only token can touch; it never changes stored content.
- Give parsers read/write tokens and ordinary users and agents that only look things up
  read-only tokens.

**Getting a token** (the admin never sees or sends it):

1. The user asks the admin for access.
2. The admin logs in at `/admin/login`, opens **Access**, enters a label for the user, picks
   read-only or read and write, and creates an invitation. SMOS shows a one-time link `/claim/<secret>` once.
3. The admin sends the link to the user.
4. The user opens the link (it states the access level) and presses **Create token**; the token
   appears once. They save it.
5. The admin's token list shows the token, its access level, and who claimed it (time, client IP,
   user agent).

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
     "summary_source": null, "section_summary": null, "section_summary_source": null,
     "document_summary": "Caps radiated emissions.",
     "document_summary_source": "parser-1", "score": 0.83, "metadata": {"type": "segment", "doc_id": "…"}}
  ],
  "sources": ["…"],
  "confidence": 0.81,
  "mode": "extractive"
}
```

`summary` is the summary attached to that exact item; `section_summary` and `document_summary` are
the ones attached to its containing section and parent document (a segment hit can show all three). Either is `null` when none was supplied. `mode` is `extractive`, or `uncertain` when
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

`/admin/login`, `/admin/dashboard` (overview: documents, segments, database size, API load and latency,
growth, CPU/memory/disk over time, most-used calls, per-token activity, recent events; ranges 1h, 24h, 7d,
30d via `?range=`), `/admin/tokens` (access: tokens, invitations), `/admin/usage` (per-token call history),
`/claim/<secret>` (public one-time token creation). Charts are drawn by SMOS itself, so the pages work offline.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SMOS_DATA_DIR` | `/data` | Where `metadata.db`, `faiss.index` and `admin.db` live |
| `SMOS_HOST` / `SMOS_PORT` | `0.0.0.0` / `8765` | Bind address |
| `SMOS_ADMIN_USER` / `SMOS_ADMIN_PASSWORD` | — | Required on first start to create the admin |
| `SMOS_INVITE_TTL_HOURS` | `72` | Invitation lifetime |
| `SMOS_USAGE_RETENTION_DAYS` | `90` | Usage-log rows older than this are pruned (at start, then daily) |
| `SMOS_METRICS_RETENTION_DAYS` | `30` | Dashboard samples (hardware and store measurements, one per minute) older than this are pruned |
| `SMOS_SAMPLE_INTERVAL_S` | `60` | Seconds between dashboard samples |
| `SMOS_USAGE_MAX_ROWS` | `1000000` | Hard cap on usage-log rows; the oldest are deleted beyond it |
| `SMOS_USAGE_PRUNE_EVERY_ROWS` | `10000` | A background prune also runs after this many new rows |
| `SMOS_UNAUTH_LOG_PER_MIN` | `60` | Log rows per minute for requests without a valid token (probing); further ones are counted, not stored |
| `SMOS_PUBLIC_URL` | request URL | Base URL put in invitation links |
| `SMOS_TRUST_PROXY` | off | `1` = use the first `X-Forwarded-For` entry as client IP |
| `SMOS_COOKIE_SECURE` | off | `1` = mark the admin cookie `Secure` (use behind HTTPS) |

The access log redacts invitation secrets (`/claim/<redacted>`). A reverse proxy in front of SMOS
has its own logs: configure it not to log `/claim/*` paths, or treat those logs as secret.

# SMOS Standalone — Project 1: Core (design)

Date: 2026-10-28. Status: draft for Governor approval (open questions answered 2026-10-28).

## Context and why

SMOS is moving from a Claude Code MCP plugin to a standalone product the Governor
will install in a Docker container inside their company for trial runs. A local agent
and one or more third-party parsers (including ones the Governor writes) talk to it
over the internal network. The built-in generative AI (Ollama / qwen2.5) is removed:
summaries and any other generated text come from the caller. SMOS stays what the
2026-10-13 "standalone / parser principle" says it is: a store plus retrieval, with
no source-system logic and no judging.

The full standalone effort is five projects, built one design at a time in this order:
**1 Core (this document)**, 2 Docker packaging, 3 full dashboard and metrics,
4 self-update, 5 documentation (grows alongside every step).

Driving use cases (context only; the 5.5c work on them is parked): authoring SYS.2
requirements and reviewing requirement bundles. In both, SMOS gathers evidence and a
local agent judges.

## Goals

1. Remove every code path that calls a generative model. `smos/llm/`, the `openai`
   dependency and all Ollama configuration are gone from the product.
2. Replace them with **caller-supplied text**: wherever a tool used to generate a
   summary, answer or reflection, the caller passes that text; SMOS validates, stores,
   links it to its source items and returns it on retrieval.
3. Expose everything over an **HTTP API** (versioned, `/api/v1`) with an OpenAPI
   description generated from the code. MCP is removed.
4. Require a **bearer token on every API call**. Tokens are created, listed and
   revoked on an admin page; every call is attributed to its token. Tokens are issued
   to users through one-time invitation links, never handed over by the admin.
5. Record **per-token usage** (endpoint, status, latency, time, payload size) so the
   admin page can show activity per token.
6. Give the data directory a **schema version and numbered migrations**, so later
   releases (project 4) can upgrade stored data in place without loss.
7. Provide a **minimal admin web page** in this project: admin login, token
   create/list/revoke, per-token usage table. The richer dashboard is project 3.

## Non-goals

- Docker image, volume layout and install guide (project 2).
- Metrics charts, hardware sampling, update checker and button (projects 3 and 4).
- Any generation, judging, classification or "verdict" inside SMOS.
- Multi-tenant isolation: one store per deployment.
- The parked 5.5c work (domain scope queries, bundle evidence, guidance doctype).

## Anti-goals (failure even if tests pass)

- A hidden fallback that still calls a model, or a silently degraded result with no
  marker that a summary was absent.
- A migration that can lose or reinterpret already-ingested data.
- An API call that works without a valid token, including the OpenAPI/docs routes
  that expose data.
- A token stored in recoverable form, or a token plaintext ever visible to the admin.
- An invitation link that works twice, never expires, or lets a stranger who guesses
  or replays it obtain a token after the intended user has claimed it.

## Decisions made by the Governor (in conversation)

- MCP is dropped; HTTP API on the internal network.
- Token auth on the API; tokens created on the admin dashboard; usage per token is
  visible there as a user-activity view.
- Tokens are issued through a one-time invitation link, so the admin never sees or
  sends a token: the user asks the admin in person; the admin logs in and creates an
  invitation (a label for the user); SMOS makes a single-use public link; the admin
  sends the link; the user opens it and presses "Create token"; the token appears on
  that page once for the user to save; the admin's token list then shows the token as
  created by the user, with timestamp, client IP and user agent.
- Tools that used the AI keep existing, taking the text from the caller.
- Summaries from a third-party parser must be accepted by the API next to the
  ingested data.
- The dashboard itself is reachable without a token; its controls need admin login.

- Development happens on `main` of this repo. The self-update feature (project 4)
  follows the remote `main`.
- The MCP-based dogfooding setup is retired. The cleanup is part of this project:
  rewrite the "SMOS active" section and MCP wording in `CLAUDE.md`, README and
  ARCHITECTURE, drop `smos/install/cli.py` and the MCP entry points, and drop the
  `smos-doc-sync` skill's dependence on the MCP server (keep it only if it can run
  against the HTTP API, otherwise remove it). The global `~/.claude` registration is
  the Governor's to remove; the plan will say how.
- There is no stored data to carry over. Migration 1 is therefore a plain baseline
  that creates the version-1 schema on an empty data directory; no 0.1.x upgrade
  path is needed. The migration framework itself is still required for later releases.

## Design (the builder's calls, marked as such; reject any)

**Stack (my call).** FastAPI + uvicorn for the API and admin page (server-rendered
HTML plus a little JS; no SPA build step). OpenAPI comes from FastAPI. Existing
modules (`VectorStore`, graph, BM25, segmenter, adapters, requirement context) are
reused unchanged where they do not touch the model. Local embeddings
(sentence-transformers) and FAISS stay: they are not generative.

**Summary field (my call).** `IngestionPayload` and each stored item gain an optional
`summary` (text) plus `summary_source` (free-form string: parser name/version). For
documents a summary may be attached to the document, to a section, or to a segment.
Retrieval responses return exact text and the stored summary side by side; a missing
summary is `null`, never fabricated. Embeddings and BM25 are computed from the exact
text as today; whether to also index summaries is a builder decision to settle by a
small retrieval-quality check, defaulting to "no".

**Tools that used the model (my call, per area).**

| Today | Standalone |
|---|---|
| `semantic_store(text)` compresses then stores | `POST /memories`: `content` + optional `summary` |
| `read_file_compress`, `write_file_safe`, `recursive_semantic_ingest`, `bulk_read`, `semantic_snapshot_repo` (server-side file paths) | Dropped. Inside a container server paths are meaningless; parsers send content through the ingestion endpoint. Code repo ingestion becomes a parser concern. |
| `compress`, `reflect` | Kept: caller posts the generated text; SMOS stores it as a memory linked to the source ids. |
| `learn`, `remember`, `forget`, `link`, `snapshot` | Kept as endpoints, no model needed. |
| `recall` / `semantic_query` returning a synthesized answer | Return ranked exact segments plus their stored summaries and graph context. No synthesized answer; `mode` is always the extractive form. |
| `ingest_document` | Kept; payload gains `summary`. |
| `requirement_context`, `graph_query`, `reconstruct`, `store_verbatim`/`retrieve` | Kept as endpoints. |

**Auth (my call).** Tokens are random 256-bit values, shown once, stored as hashes,
with name, created time, last-used time and a revoked flag. Admin accounts: a first
admin is bootstrapped from environment variables on first start; passwords are
stored hashed; sessions use secure cookies. A single admin role is enough for the
trial.

**Invitation links (my call on the details).** An invitation has a label, a creator
(the admin), an expiry (default 72 hours, `SMOS_INVITE_TTL_HOURS`), and a random
256-bit secret that appears only inside the link shown to the admin once; only its
hash is stored. The public claim page `/claim/<secret>` needs no login. Claiming is
atomic and single-use: the first successful claim consumes the invitation; later
visits, expired or revoked invitations and wrong secrets show the same neutral
"link not valid" page. The created token inherits the invitation's label, and the
admin's token list records who claimed it (claim time, client IP, user agent). The
client IP is the direct peer address unless `SMOS_TRUST_PROXY=1`, in which case the
first `X-Forwarded-For` entry is used. Admins can revoke an unclaimed invitation.
The old "admin creates a token directly" path is removed.

**Usage log (my call).** A row per API call in a SQLite table separate from the
memory store, with a configurable retention in days (default 90) and a nightly prune.

**Versioning and migrations (my call).** A `schema_version` table in the metadata
database and a `migrations/` list of numbered, forward-only steps run at startup
before serving. The first migration is a baseline that creates the version-1 schema
(no data to upgrade, see Governor decisions). The FAISS index and BM25 data are treated as rebuildable from
SQLite. A pre-migration backup is taken by the migration runner. The API is
versioned in the URL; additive changes only within `v1`.

**Packaging name.** The distribution is renamed away from `smos-mcp`; exact name is
the builder's call.

## What stays open for the builder

Endpoint naming, error format, pagination, exact table layouts, session mechanics,
the summary-indexing check above, and how existing tests are ported (the 381-test
suite should keep passing where the behavior is unchanged; tests for removed tools
are deleted, not skipped).

## Risks to watch

- Retrieval quality without compressed text: current benchmarks used summarized
  segments in places. Re-run the legal and toolchain benches after the change and
  record the delta.
- `CLAUDE.md`, the handoff and the runbooks assume the MCP server; the cleanup in
  the Governor decisions above must leave no stale instructions behind.

# SMOS — Semantic Memory Operating System

A self-hosted store that keeps documents **exactly** and retrieves them by meaning
(vector + keyword + graph), over an authenticated HTTP API. SMOS never generates text:
a parser or agent supplies any summaries, and SMOS returns them beside the exact source
text.

It is built for corpora such as requirements, standards and regulations, where a local
agent needs the *right* passages (with their links and citations) rather than a paraphrase.

## What it does

- **Exact storage and retrieval.** Segments keep their original text; `GET /nodes/{id}/text`
  rebuilds a document byte for byte.
- **Hybrid search.** Local embeddings (sentence-transformers + FAISS), BM25 keyword search
  and Reciprocal Rank Fusion, plus graph expansion over references.
- **A typed graph.** Documents, sections, segments and the links parsers declare
  (`depends_on`, `parent_of`, `cites`, …).
- **Requirement evidence.** `POST /requirement-context` gathers linked items, similar items,
  relevant standard clauses and coverage facts for a requirement. It gives no verdict.
- **Caller-supplied summaries.** Send them with the document or attach them later to any node.
- **Token authentication.** Tokens are issued through one-time invitation links; the
  admin dashboard shows each token's usage.

## Quick start (development)

```bash
pip install -e ".[dev]"
export SMOS_DATA_DIR=./data SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD='choose-one'
smos-server                      # serves http://localhost:8765
```

1. Open `http://localhost:8765/admin/login` and log in.
2. Under **Access**, create an invitation and send the link to the user.
3. The user opens the link, presses **Create token** and saves the token.
4. Call the API:

```bash
curl -s http://localhost:8765/api/v1/query \
  -H "Authorization: Bearer $SMOS_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "radiated emissions limits", "k": 5}'
```

The machine-readable API description is at `/api/v1/openapi.json` (needs a token or an
admin session). The human summary is [docs/API.md](docs/API.md); the internals are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Status

SMOS 0.2 is the first standalone release: the built-in AI and the MCP server were removed.
Docker packaging, the full metrics dashboard, self-update and the complete user guide are
the next projects; see [docs/HANDOFF.md](docs/HANDOFF.md) and
`docs/superpowers/specs/2026-10-28-standalone-core-design.md`.

## Tests

```bash
pytest tests/ -q
```

# SMOS — Project Instructions

SMOS (Semantic Memory Operating System) is a self-hosted store with exact-text retrieval
(FAISS vectors + SQLite metadata + BM25 + a typed graph) behind an authenticated HTTP API
(`smos/api`, FastAPI). **SMOS never calls a generative model**: summaries are supplied by
the caller (a third-party parser or agent), stored beside the exact text, and returned with
it. Since 0.2 there is no MCP server and no Ollama dependency. See `README.md` for the
overview, `ARCHITECTURE.md` for data flow, locking and failure modes, `docs/API.md` for the
endpoint summary.

## Current state & handoff

**Read `docs/HANDOFF.md` first, every session.** It holds the current project, what shipped
last session, and next steps. **Update it at the end of every session** (what changed,
what's next) so a fresh session can orient from that file alone.

The standalone effort is five projects, built one design at a time:
1 Core (done) → 2 Docker packaging → 3 full dashboard and metrics → 4 self-update →
5 documentation. Design records live in `docs/superpowers/specs/`, plans in
`docs/superpowers/plans/`.

## Working on this repo

- This repo is no longer its own memory tool: there is no SMOS file-reading policy. Use the
  normal Read / Grep / Glob tools.
- Develop on `main`. The updater (project 4) will follow the remote `main`.
- Data directory (`SMOS_DATA_DIR`) holds `metadata.db`, `faiss.index` and `admin.db`;
  storage schemas are versioned and migrated by `smos/storage/migrations.py`. Any change to a
  table needs a new numbered migration, never an edit of an existing one.
- Auth is non-negotiable: every `/api/v1` route except `/health` must depend on
  `require_token`; `tests/test_api_auth.py::test_every_route_in_schema_is_protected` enforces
  it. Tokens are shown once and stored as hashes; the admin never sees a token.

## Workflow

- **Governor** — the user. Sets direction and priorities, approves plans, makes the final
  call on anything ambiguous or risky.
- **Orchestrator** — this main session. Plans and architects the work, writes and reviews
  code directly, and reviews every diff before it is considered done.
- **Implementation** — inline by default (the Governor chose inline execution for the
  standalone work). If work is delegated to `Agent` calls, each dispatch must be
  self-contained (exact paths, verified current signatures, acceptance criteria), and the
  orchestrator reviews the diff.

## Tests

```bash
pytest tests/ -q
```

`pytest.ini` / `pyproject.toml` set `python_files = ["test_*.py"]` — files named `bench_*.py`
are standalone `__main__` scripts, not pytest-collectible. On this machine a bare `pytest`
sometimes reports "No tests collected"; retry with `rtk proxy pytest tests/ -q`. Use `python`
or `pytest`, never `python3` (Windows Store stub).

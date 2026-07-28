# SMOS — Project Instructions

SMOS (Semantic Memory Operating System) is an MCP server (`smos-mcp`, v0.1.4) that
gives Claude Code a persistent memory layer: FAISS vector search + SQLite metadata +
local-LLM (Ollama/qwen2.5) compression, with BM25 hybrid retrieval (RRF fusion) added
in Phase 1. It lets Claude compress files and prior analysis out of the context window
and query them back by meaning instead of re-reading raw source. See `README.md` for
the pitch and tool list, `ARCHITECTURE.md` for data flow / locking / failure modes.

This repository *is* the SMOS product. Its own source is therefore also, confusingly,
a valid target for its own tools — as of 2026-07-28 this project opts into using them
on itself as a live dogfooding test, see "SMOS active — file reading policy" below.

## Current state & handoff

**Read `docs/HANDOFF.md` first, every session.** It holds the current phase, what
shipped last session, and next steps. **Update it at the end of every session**
(what changed, what's next) so a fresh session can orient from that file alone.
**Then run the `smos-doc-sync` skill** (`.claude/skills/smos-doc-sync/`) to
re-ingest any changed docs — including this file — into SMOS before ending
the session.

Other references:
- `ARCHITECTURE.md` — data flow, locking, failure modes, scaling ceilings; kept
  current with each phase (self-analysis document).
- `docs/superpowers/plans/2026-06-30-smos-roadmap.md` — the 4-phase roadmap
  (BM25 hybrid → Memory Graph → Agent Primitives → HNSW).

## SMOS active — file reading policy

This repo uses its own SMOS tools for ordinary file reading (opted into 2026-07-28
as a live dogfooding test — this **replaces** the previous "validation only, never
general reading" restriction that used to apply specifically to this repo, and now
matches the standard global `~/.claude/CLAUDE.md` SMOS policy instead of overriding
it).

For any file you are NOT about to edit, use `tool_read_file_compress` instead of
the built-in Read tool. Pass the absolute path. The full file content will NOT
enter the context window — only a compressed summary is returned and stored.

Use the built-in Read tool ONLY immediately before an Edit or Write call.

Before reading any file, call `tool_semantic_query` first. If the query returns
useful context, skip the read entirely.

Storage routing:
- Prose, analysis, notes → `tool_semantic_store` (LLM-compressed, queryable)
- Code, diffs, exact text → `tool_store_verbatim` (lossless, retrieve by key)

**Known risk, watch for it:** `tool_read_file_compress` hallucinated a nonexistent
`_init_db_and_index()` method when compress-reading `vector_store.py` during Phase 1
(see the `bm25-phase1-lessons` cross-session memory). That's exactly the kind of
failure this live test exists to surface, not avoid — but it means a compressed
summary of this repo's own code is not automatically trustworthy. Spot-check it
against the real source (built-in `Read`) before relying on it for anything
security- or logic-critical, and always before it informs an `Edit`.

## Workflow

- **Governor** — the user. Sets direction and priorities, approves plans, makes the
  final call on anything ambiguous or risky.
- **Orchestrator** — this main session. Plans and architects the work, writes and
  reviews code directly for anything complex, sensitive, or judgment-heavy,
  decomposes the remaining work into self-contained implementation tasks, and reviews
  every diff before it's considered done.
- **Implementation** — delegated to `Agent` calls with `model: "haiku"`, dispatched
  and coordinated by the orchestrator. Haiku agents don't share this session's
  context, so each dispatch must be self-contained: exact file paths, *current*
  signatures the orchestrator has verified by reading the real file first (don't hand
  a Haiku agent a stale plan snippet — Phase 1's plan drifted from the real tree on
  every one of its 4 tasks; see `bm25-phase1-lessons`), and concrete acceptance
  criteria. Orchestrator reviews the diff before integrating; don't let a Haiku
  agent's self-report stand in for verification.

## Tests

```bash
pytest tests/ -v --tb=short
```

`pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are
**not** pytest-collectible; they're standalone `__main__` scripts. Don't route a
regression gate through a `bench_*.py` file and assume `pytest` runs it.

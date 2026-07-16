# SMOS — Project Instructions

SMOS (Semantic Memory Operating System) is an MCP server (`smos-mcp`, v0.1.4) that
gives Claude Code a persistent memory layer: FAISS vector search + SQLite metadata +
local-LLM (Ollama/qwen2.5) compression, with BM25 hybrid retrieval (RRF fusion) added
in Phase 1. It lets Claude compress files and prior analysis out of the context window
and query them back by meaning instead of re-reading raw source. See `README.md` for
the pitch and tool list, `ARCHITECTURE.md` for data flow / locking / failure modes.

This repository *is* the SMOS product. Its own source is therefore also, confusingly,
a valid target for its own tools — see "SMOS tool usage" below for why that's handled
specially here.

## Current state & handoff

**Read `docs/HANDOFF.md` first, every session.** It holds the current phase, what
shipped last session, and next steps. **Update it at the end of every session**
(what changed, what's next) so a fresh session can orient from that file alone.

Other references:
- `ARCHITECTURE.md` — data flow, locking, failure modes, scaling ceilings; kept
  current with each phase (self-analysis document).
- `docs/superpowers/plans/2026-06-30-smos-roadmap.md` — the 4-phase roadmap
  (BM25 hybrid → Memory Graph → Agent Primitives → HNSW).

## SMOS tool usage — validation only, never general reading

Do **not** use this repo's own SMOS MCP tools (`tool_read_file_compress`,
`tool_semantic_query`, `tool_semantic_store`, `tool_store_verbatim`, `tool_bulk_read`,
`tool_recursive_semantic_ingest`, `tool_semantic_snapshot_repo`, etc.) for ordinary
file reading, code exploration, or session bootstrapping in this repository. Use the
built-in `Read` / `Grep` / `Glob` tools instead — always, for every file.

**This overrides the global `~/.claude/CLAUDE.md` SMOS file-reading policy for work
inside this repo.**

**Why:** this repo is the thing that implements those tools. Using them on their own
source is uncontrolled dogfooding — self-referential and hard to sanity-check. It
already caused a real bug: `tool_read_file_compress` hallucinated a nonexistent
`_init_db_and_index()` method when compress-reading `vector_store.py` during Phase 1
(see the `bm25-phase1-lessons` cross-session memory). A direct `Read` doesn't have
that failure mode, and the token savings the tool exists to provide aren't worth
seeded confusion about this codebase's own shape.

**When SMOS tools *are* appropriate here:** only when SMOS itself is the subject under
test — verifying `tool_semantic_query` returns expected results for a benchmark,
checking compression fidelity/recall, exercising the MCP surface end-to-end for a
regression test. That's validating the product; it is not a substitute for reading
the codebase that builds it.

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

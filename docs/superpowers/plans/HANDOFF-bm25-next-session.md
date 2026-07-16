> **SUPERSEDED — 2026-07-16.** Phase 1 (BM25 hybrid retrieval) shipped 2026-07-02 at commit `b890a19`.
> This file is kept only as the historical pre-implementation plan. For current project state,
> see **`docs/HANDOFF.md`** (updated at the end of every session) instead of this file.

# BM25 Hybrid Retrieval — Session Handoff (historical)

**Project:** SMOS (Semantic Memory Operating System)
**Working directory:** `c:\Private\semantic_memory`
**Date:** 2026-06-30
**Next action:** Implement BM25 hybrid retrieval (Phase 1 of Memory OS roadmap)

---

## Where We Are

SMOS is an MCP server providing semantic memory to AI agents. It reached v0.1.4 with:
- SQLite metadata store + FAISS `IndexFlatIP` vector store
- `all-MiniLM-L6-v2` embeddings (384-dim)
- Ollama + `qwen2.5:7b` for summarization
- Hot/warm/cold memory tiers
- Recursive repository ingestion + semantic snapshots
- Benchmarks all at 100/100 except retrieval (~90) and compression (~85)

We just completed a full architectural review. The conclusion: **the single highest-ROI next step is BM25 hybrid retrieval**, because:
- Pure vector search fails on exact-term queries (function names, error codes, identifiers)
- BM25 is purely additive — zero regression risk
- ~1 session to implement
- Lays the retrieval quality foundation for Phase 2 (Memory Graph)

---

## What to Build

**Reciprocal Rank Fusion hybrid:** run BM25Okapi alongside FAISS vector search, merge both ranked lists via RRF (`score = 1/(60+rank_vector) + 1/(60+rank_bm25)`).

### Files that change

| File | Change |
|------|--------|
| `smos/memory/bm25_index.py` | **Create** — BM25Index class (thread-safe, in-memory, rebuilt from SQLite on startup) |
| `smos/memory/vector_store.py` | **Modify** — add `self.bm25` field, seed on init, update on store/delete |
| `smos/compression/context_builder.py` | **Modify** — add `_rrf_merge()`, replace fixed 70/30 rerank, remove dead `_classify_domain` + `_rerank` |
| `tests/test_bm25_index.py` | **Create** — unit tests for BM25Index |
| `tests/test_hybrid_retrieval.py` | **Create** — integration test asserting exact-term ranking |
| `tests/bench_retrieval.py` | **Modify** — add `test_exact_term_retrieval_quality` regression gate (≥0.80 recall) |

### New dependency
```
pip install rank-bm25
```

---

## The Full Plan

**`docs/superpowers/plans/2026-06-30-bm25-hybrid-retrieval.md`**

It contains complete test code, implementation code, and exact commands for every step. Four tasks:

1. **BM25Index class** — build, update, remove, query with thread safety
2. **Wire into VectorStore** — seed from SQLite on init, update on every store/delete
3. **Hybrid retrieval in context_builder.py** — RRF merge, remove dead code
4. **Exact-term benchmark** — regression gate proving BM25 is active

---

## How to Execute

Open Claude Code in `c:\Private\semantic_memory` and say:

> "Read `docs/superpowers/plans/2026-06-30-bm25-hybrid-retrieval.md` and execute it using superpowers:executing-plans, starting with Task 1."

Or use `superpowers:subagent-driven-development` for task-by-task subagent execution with review gates.

---

## Verification After Completion

```bash
pytest tests/ -v --tb=short
```

All existing benchmarks must pass. The new `test_exact_term_retrieval_quality` must score ≥ 0.80 recall.

---

## What Comes Next (Phase 2)

Memory Graph — `docs/superpowers/plans/2026-06-30-smos-roadmap.md` Phase 2 section.

SQLite graph tables (`graph_nodes`, `graph_edges`), entity extractor (Python AST + regex), graph expansion in retrieval pipeline, `tool_graph_query` MCP tool. Transforms SMOS from semantic retrieval into knowledge retrieval.

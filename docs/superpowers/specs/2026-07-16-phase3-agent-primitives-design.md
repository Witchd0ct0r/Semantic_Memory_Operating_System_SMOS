# Phase 3 — Agent Memory Primitives: Design

**Status:** Approved, ready for implementation planning.
**Roadmap pointer:** `docs/superpowers/plans/2026-06-30-smos-roadmap.md`, "Phase 3" section (high-level sketch — this doc supersedes it as the source of truth for Phase 3 specifics).
**Prior phases:** Phase 1 (BM25 hybrid retrieval, merged `b890a19`), Phase 2 (Memory Graph, merged `fa3ed64`). See `docs/HANDOFF.md`.

## Scope

Full Phase 3 in one spec: the 8 agent-primitive MCP tools, the new retrieval strategies inside `recall()`, and the tier-promotion fix — designed together because `recall()`'s `strategy` param depends on the retrieval-mode work, and tier tracking is threaded through every retrieval path added here.

Explicitly **out of scope** for this phase:
- Tier *enforcement* (filtering/evicting cold-tier memories from search) — this phase only fixes how tiers are computed, not their behavioral effect. Tracked as follow-up tech debt.
- Confidence-weighted retrieval scoring — `confidence` is stored and returned but does not affect ranking math this phase.
- Retroactive backfill of `source_ids`/`confidence`/tier-tracking columns for memories stored before this phase (same posture as Phase 2's graph backfill gap — new rows only).

## 1. Data model changes

`MemoryObject` (`smos/memory/schemas.py`) gains two optional fields, both no-ops for existing callers:
- `source_ids: list[str] = []` — populated by `compress()`/`reflect()` to link a derived memory back to its inputs.
- `confidence: float = 1.0` — populated by `learn()`; stored/returned only, no scoring effect this phase.

`memories` table (`smos/memory/vector_store.py`, `_init_db`) gains four columns via the existing `ALTER TABLE ... ADD COLUMN` + `except sqlite3.OperationalError: pass` pattern already used for `tier`:
- `source_ids TEXT NOT NULL DEFAULT ''` (comma-joined, mirrors how `tags` is stored)
- `confidence REAL NOT NULL DEFAULT 1.0`
- `recall_count INTEGER NOT NULL DEFAULT 0`
- `last_recalled TEXT` (nullable ISO-8601 timestamp)

## 2. New modules & files

| File | Action | Responsibility |
|------|--------|-----------------|
| `smos/tools/agent_primitives.py` | Create | `remember`, `forget`, `compress`, `reflect`, `learn`, `link`, `snapshot`, `recall` business logic — plain functions taking explicit params + `store`, mirroring `smos/tools/semantic_tools.py`'s pattern (no MCP decorators here) |
| `smos/memory/recall_engine.py` | Create | `recall(query, strategy, k, since, until, seed_id, component, store)` — strategy dispatch |
| `smos/memory/schemas.py` | Modify | Add `source_ids`, `confidence` to `MemoryObject` |
| `smos/memory/vector_store.py` | Modify | Add `source_ids`/`confidence` columns + constructor/`store()` params; add `recall_count`/`last_recalled` columns; add `bump_recall(uuids: list[str])` batched-update method |
| `smos/memory/lifecycle.py` | Modify | Replace positional `_promote_tiers()` with frequency-based thresholds |
| `smos/tools/semantic_tools.py` | Modify | `semantic_query`/`graph_query` call `store.bump_recall(...)` on the ids they return |
| `smos/llm/summarizer.py` | Modify | Add a `reflect`-specific synthesis prompt function, distinct from `compress_memories_full`'s summarize-focused prompt |
| `smos/server.py` | Modify | Register 8 new `@app.tool()` wrappers, same try/except-and-stringify-error convention as existing tools |
| `tests/test_agent_primitives.py` | Create | Unit tests per primitive |
| `tests/test_recall_engine.py` | Create | Unit tests per strategy |
| `tests/test_lifecycle_tiers.py` | Create | Unit tests for frequency-based promotion thresholds |
| `tests/bench_phase3_primitives.py` | Create | Standalone (non-pytest) benchmark, matching the `bench_*.py` convention |

## 3. Primitive semantics

- **`remember(content, type="episodic")`** — thin wrapper over `store.store()`, same as `semantic_write` but named for the agent-memory framing.
- **`forget(id)`** — `store.delete(id)`; `VectorStore.delete()` already removes the memory's graph node + edges via `graph.remove_node()` — reused as-is, no new graph-cleanup logic needed.
- **`link(id_a, id_b, edge_type)`** — validates both ids exist as `memory` nodes, then `store.graph.add_edge(id_a, id_b, edge_type)`. `edge_type` restricted to the existing `GraphEdge` Literal set (`references`, `depends_on`, `related_to`, `implements`, `contains`, `evolves_into`, `generated_from`).
- **`snapshot()`** — thin wrapper delegating to the existing `do_semantic_snapshot_repo`, defaulting `path` to `server.py`'s already-known `_PROJECT_ROOT` so the primitive needs no path argument (unlike the existing raw `tool_semantic_snapshot_repo`, which requires one).
- **`learn(content)`** — same plumbing as `remember`, but sets `confidence=1.0` explicitly and `type="semantic"` (vs. `remember`'s `"episodic"`) — differs from `remember` only in the defaults passed to `MemoryObject`.
- **`compress(ids)`** — fetches each source via `get_by_uuid`, calls the existing `compress_memories_full()` (same LLM merge `build_compressed_context` already uses) over their contents, stores the result with `type="compressed"`, `source_ids=ids`, and adds a `generated_from` graph edge from the new memory to each source. **Sources are kept, not deleted** — non-destructive, consistent with the project's additive-only bias from Phases 1/2.
- **`reflect(query)`** — runs hybrid retrieval for `query` (reuses `build_compressed_context`'s candidate-gathering, not its LLM call), then calls the LLM with a new "reason about what you know — note gaps/contradictions/implications" prompt (new function in `smos/llm/summarizer.py`), stores as `type="reflection"`, `source_ids=[retrieved ids]`, and **is stored back** into the corpus (not ephemeral) so reflections form a durable trail of the agent's own reasoning.
- **`recall(query, strategy="hybrid", k=5, since="", until="", seed_id="", component="")`** — single MCP tool covering all strategies (see §4). Empty-string params are "unset," matching the existing `tags=""` convention on `tool_semantic_query`.

## 4. `recall_engine.py` strategy dispatch

```python
def recall(query, strategy, k, since, until, seed_id, component, store) -> dict:
    if strategy == "temporal":
        return _recall_temporal(query, k, since, until, store)
    if strategy == "chain":
        return _recall_chain(seed_id, k, store)
    if strategy == "repo":
        return _recall_repo(query, k, component, store)
    return build_compressed_context(query, k, store).model_dump()  # "hybrid" (default)
```

- **`_recall_temporal`** — SQL `WHERE timestamp BETWEEN ? AND ?` against `memories` (empty `since`/`until` = open-ended bound), then reranks the matched subset by vector similarity to `query` (embed once, cosine against the small filtered candidate set — no FAISS index needed since SQL already bounds the set).
- **`_recall_chain`** — `store.graph.traverse(seed_id, max_hops=k, edge_types=["evolves_into", "related_to"])`, filtered to `type == "memory"` nodes, hydrated via `get_by_uuid`. Note: `k` means hop depth for this strategy, not result count — documented explicitly in the tool docstring to avoid confusion with every other strategy's `k`.
- **`_recall_repo`** — thin alias: `build_compressed_context(query, k, store, tags=[component])`, since that function already accepts `tags`. Not new retrieval logic.
- **`hybrid`** (default) — direct delegation to the existing, untouched `build_compressed_context()`. `context_builder.py` is not modified by this phase (see Architecture Decision below).

**Return shape:** all strategies return `CompressedContext.model_dump()`'s shape (`summary`, `sources`, `confidence`, `mode`) **except `chain`**, which has no natural LLM summary (pure traversal, no LLM call): it returns `{"sources": [...], "chain": [...], "confidence": 1.0, "mode": "extractive"}`. This is the one documented shape exception.

**Tier tracking:** `recall()`'s top-level function calls `store.bump_recall(sources)` once after dispatch, regardless of strategy — individual `_recall_*` functions don't need to know about tier tracking.

### Architecture decision: why a separate `recall_engine.py` instead of extending `context_builder.py`

Considered extending `build_compressed_context()` directly with a `strategy` param instead. Rejected because `context_builder.py` is the function Phase 1's exact-term-recall benchmark and Phase 2's graph-retrieval benchmark already gate on — touching it risks regressing already-verified behavior for strategies (`temporal`, `chain`) that don't need vector/BM25/graph-expansion at all. A separate module keeps the hybrid path byte-for-byte identical to today and each new strategy independently testable in isolation.

## 5. Tier fix (`smos/memory/lifecycle.py`)

Replace `_promote_tiers()`'s positional slicing (`uuids[:cold_cut]`, `uuids[cold_cut:warm_cut]`) with frequency/recency thresholds computed from the new `recall_count`/`last_recalled` columns:

```python
_HOT_RECALL_COUNT = 10
_HOT_RECENCY_DAYS = 7
_WARM_RECALL_COUNT = 3
_WARM_RECENCY_DAYS = 30
```

- `hot`: `last_recalled` within 7 days OR `recall_count > 10`
- `warm`: `last_recalled` within 30 days OR `recall_count > 3` (and not hot)
- `cold`: everything else

Implemented as one SQL `UPDATE ... CASE WHEN ... END` statement against `memories` (single round-trip, avoids the current per-row Python-loop `updates` list), run inside the same background lifecycle cycle as today (`on_insert` → `_run_cycle` → `_promote_tiers` + `_deduplicate`) — stays off the write-path hot loop. `_MIN_STORE_SIZE` gate is unchanged.

**No enforcement this phase** — tiers still don't filter search or drive eviction; this only fixes how the label is computed. Filtering/eviction is out of scope (see Scope section) and remains tracked as follow-up tech debt.

**Tracking scope:** every retrieval path bumps `recall_count`/`last_recalled` for the memories it returns — `tool_semantic_query`, `tool_graph_query`, and the new `recall()` all call `store.bump_recall(...)`. Tier reflects actual usage regardless of which tool an agent calls, not just the new primitive surface.

## 6. MCP tool registration & error handling

8 new `@app.tool()` functions in `smos/server.py`, following the exact existing convention: docstring with `Args:`, try/except wrapping the call into `agent_primitives.py`/`recall_engine.py`, stringified `{"error": f"{type(exc).__name__}: {str(exc)[:200]}"}` on failure. `tool_recall`'s empty-string params (`since`, `until`, `seed_id`, `component`) follow the same "empty string = unset" convention already used by `tags=""` on `tool_semantic_query`. No new error-handling pattern is introduced.

## 7. Testing & benchmarks

- **`tests/test_agent_primitives.py`** — `remember`/`learn` produce expected `type`/`confidence`; `forget` removes graph node+edges (regression check on existing `delete()` behavior); `link` rejects invalid `edge_type` and nonexistent ids; `compress` produces `source_ids` matching inputs plus verifiable `generated_from` edges; `reflect` produces `source_ids` and output distinguishable from `compress`'s (different prompt); `snapshot` defaults path to project root correctly.
- **`tests/test_recall_engine.py`** — `temporal` respects `since`/`until` bounds (inclusive/open-ended cases); `chain` follows only `evolves_into`/`related_to`, never `references`; `repo` filters by tag; `hybrid` output is identical to calling `build_compressed_context` directly (proves the delegation is a true passthrough, not a reimplementation).
- **`tests/test_lifecycle_tiers.py`** — seed `recall_count`/`last_recalled` fixtures, assert tier labels match the threshold table in §5; regression-check `_deduplicate` still runs after promotion within the same cycle.
- **`tests/bench_phase3_primitives.py`** (standalone, non-pytest — see `pytest.ini`'s `test_*.py` collection rule) — latency of each primitive and each `recall()` strategy, matching the style of `bench_graph_retrieval.py`/`bench_phase2_vs_phase1.py`.
- Full existing suite (`pytest tests/ -v --tb=short`, currently 142 passing) must stay green. `context_builder.py` is untouched by this phase, so Phase 1/2 benchmark gates shouldn't be affected — re-run anyway to catch accidental import-order or column-migration regressions.

## Global constraints (carried from roadmap, still apply)

- All existing MCP tools remain functional and backward-compatible.
- SQLite schema changes via `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN` only — no destructive migrations.
- Existing `.smos/` data directories load without manual intervention (new columns default such that pre-existing rows are valid).
- Windows-compatible paths throughout (`pathlib.Path`).
- No new required runtime dependencies.

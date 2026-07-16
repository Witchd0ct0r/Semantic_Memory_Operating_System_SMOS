# SMOS Evolution Roadmap — Memory OS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve SMOS from a semantic-memory MCP into a true Memory Operating System supporting long-term agent cognition, repository understanding, and multi-agent coordination.

**Architecture:** Three-phase evolution — (1) fix retrieval quality with BM25 hybrid search, (2) add a graph layer over SQLite for entity/relationship knowledge, (3) extend the MCP surface with graph traversal tools and agent memory primitives. Each phase is independently deployable and backward-compatible.

**Tech Stack:** Python, FastMCP, FAISS IndexFlatIP → HNSW, SQLite + recursive CTEs, `rank_bm25` (BM25Okapi), `sentence-transformers` (all-MiniLM-L6-v2), Ollama + qwen2.5:7b, Pydantic v2.

## Global Constraints

- All existing benchmarks must pass after every phase: `bench_retrieval`, `bench_security`, `bench_mcp_tools`, `bench_performance`, `bench_compression`
- All existing MCP tools must remain functional and backward-compatible
- No new required runtime dependencies beyond `rank_bm25` (Phase 1) and stdlib only (Phase 2 graph)
- SQLite schema changes via `CREATE TABLE IF NOT EXISTS` only — no destructive migrations
- Existing `.smos/` data directories must load without manual intervention
- Windows-compatible paths throughout (use `pathlib.Path`)
- Python 3.10+

---

## Phase 1 — BM25 Hybrid Retrieval

**Reference plan:** `docs/superpowers/plans/2026-06-30-bm25-hybrid-retrieval.md`

**Summary:** Wire BM25Okapi into the retrieval pipeline alongside FAISS vector search. Replace the fixed 70/30 formula in `context_builder.py` with a proper reciprocal-rank-fusion hybrid score. Add a BM25 index maintained in-memory from the SQLite corpus. Benchmark before/after retrieval quality.

**Status:** Ready to implement — start here.

---

## Phase 2 — Memory Graph (Entity Extraction + SQLite Graph Layer)

**Estimated sessions:** 2–3

### Why Memory Graph transforms SMOS

SMOS currently retrieves *similar text*. The Memory Graph makes SMOS retrieve *connected knowledge*. The difference:

- Vector search: "auth" → memories that semantically resemble "auth"
- Graph retrieval: "auth" → memories → entities → JWT → middleware → auth_service → login_endpoint (via relationship traversal)

### Files to create/modify

| File | Action | Responsibility |
|------|--------|---------------|
| `smos/memory/graph_store.py` | Create | Graph schema init, node/edge CRUD, traversal queries |
| `smos/memory/entity_extractor.py` | Create | Extract entities from memory content (AST + regex) |
| `smos/memory/vector_store.py` | Modify | Call graph update on every `store()` |
| `smos/memory/schemas.py` | Modify | Add `GraphNode`, `GraphEdge` Pydantic models |
| `smos/compression/context_builder.py` | Modify | Graph expansion step after vector candidates |
| `smos/tools/semantic_tools.py` | Modify | Expose `tool_graph_query` MCP tool |
| `smos/server.py` | Modify | Register graph store, wire new tool |
| `tests/test_graph_store.py` | Create | Unit tests for graph schema and traversal |
| `tests/test_entity_extractor.py` | Create | Unit tests for entity extraction |
| `tests/bench_graph_retrieval.py` | Create | Benchmark: graph-augmented vs vector-only retrieval |

### Graph Schema (SQLite)

```sql
CREATE TABLE IF NOT EXISTS graph_nodes (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent')),
    name       TEXT NOT NULL,
    metadata   TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    source_id  TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    target_id  TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    edge_type  TEXT NOT NULL CHECK(edge_type IN (
                   'references','depends_on','related_to',
                   'implements','contains','evolves_into','generated_from'
               )),
    weight     REAL DEFAULT 1.0,
    metadata   TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_edges_source    ON graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target    ON graph_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type      ON graph_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_nodes_type_name ON graph_nodes(type, name);
```

### Node Types

| Type | Purpose | Example |
|------|---------|---------|
| `entity` | Named code/domain object | `JWTValidator`, `auth_service`, `login_endpoint` |
| `concept` | Abstract domain idea | `authentication`, `caching`, `rate limiting` |
| `memory` | FK mirror of a MemoryObject | (mirrors `memories.uuid`) |
| `repo_component` | File or module | `smos/memory/vector_store.py` |
| `snapshot` | Point-in-time repo state | snapshot UUID |
| `agent` | Agent session | session UUID |

### Edge Types

| Type | Meaning | Example |
|------|---------|---------|
| `references` | Memory mentions entity | memory → JWTValidator |
| `depends_on` | Import/dependency | auth_service → jwt_lib |
| `related_to` | Semantic sibling | authentication ↔ session management |
| `implements` | Component realises concept | auth_service → authentication |
| `contains` | Parent contains child | vector_store.py → VectorStore |
| `evolves_into` | Newer version of | memory_v1 → memory_v2 |
| `generated_from` | Derived from ingestion | memory → repo_component |

### Entity Extraction Strategy

```python
# Python code: AST-based
import ast
def extract_python_entities(source: str) -> list[str]:
    tree = ast.parse(source)
    entities = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            entities.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                entities.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                entities.append(node.module.split(".")[0])
    return list(set(entities))

# General prose: regex-based
import re
def extract_prose_entities(text: str) -> list[str]:
    # CamelCase identifiers, snake_case with underscores, ALL_CAPS constants
    patterns = [
        r'\b[A-Z][a-zA-Z]{2,}\b',          # CamelCase
        r'\b[a-z][a-z_]{2,}[a-z]\b',        # snake_case (min 4 chars)
        r'\b[A-Z][A-Z_]{2,}[A-Z]\b',        # CONSTANTS
    ]
    entities = []
    for pattern in patterns:
        entities.extend(re.findall(pattern, text))
    return list(set(entities))
```

### Hybrid Retrieval with Graph Expansion

```
query
  → vector candidates (k*4)
  → BM25 candidates (k*4)               ← from Phase 1
  → hybrid merge + rerank (top k*2)
  → graph expansion: for each candidate memory
      → find referenced entities (1-hop)
      → find other memories referencing same entities (2-hop)
  → merge expanded set, deduplicate
  → final rerank: 0.5 * hybrid_score + 0.3 * graph_centrality + 0.2 * term_overlap
  → top k → LLM compression
```

### New MCP Tool

```python
@app.tool()
def tool_graph_query(entity: str, hops: int = 2) -> dict:
    """Traverse the memory graph from a named entity.
    Returns connected memories and their relationships."""
```

### Task Breakdown

#### Task 2.1 — Graph Schema + GraphStore

- [ ] Add `graph_nodes` and `graph_edges` tables to `VectorStore._init_db_and_index()`
- [ ] Create `smos/memory/graph_store.py` with `GraphStore` class
- [ ] Methods: `upsert_node()`, `insert_edge()`, `get_neighbors()`, `get_memories_for_entity()`
- [ ] Write `tests/test_graph_store.py` — test node upsert, edge insert, neighbor traversal
- [ ] Run tests: `pytest tests/test_graph_store.py -v` → all pass
- [ ] Commit: `feat: add SQLite graph schema and GraphStore`

#### Task 2.2 — Entity Extractor

- [ ] Create `smos/memory/entity_extractor.py`
- [ ] Implement `extract_entities(content: str, content_type: str) -> list[str]`
  - Python source: AST-based (class names, function names, imports)
  - General prose: regex-based (CamelCase, snake_case identifiers, error codes)
- [ ] Write `tests/test_entity_extractor.py` — test Python source, prose, edge cases
- [ ] Run tests: `pytest tests/test_entity_extractor.py -v` → all pass
- [ ] Commit: `feat: add entity extractor (AST + regex)`

#### Task 2.3 — Wire Graph Update into VectorStore.store()

- [ ] Add `GraphStore` field to `VectorStore.__init__()`
- [ ] After every `store()`: extract entities, upsert graph nodes, insert `references` edges
- [ ] Run full benchmark suite to verify no regression
- [ ] Commit: `feat: wire entity extraction into VectorStore.store()`

#### Task 2.4 — Graph Expansion in context_builder.py

- [ ] After vector+BM25 candidates, add graph expansion step
- [ ] Fetch connected entities for each candidate memory
- [ ] Fetch other memories referencing the same entities (2-hop)
- [ ] Final rerank: `0.5 * hybrid_score + 0.3 * graph_centrality + 0.2 * term_overlap`
- [ ] Benchmark: `bench_graph_retrieval.py` — compare graph-augmented vs baseline
- [ ] Commit: `feat: graph expansion in retrieval pipeline`

#### Task 2.5 — tool_graph_query MCP Tool

- [ ] Add `tool_graph_query(entity: str, hops: int = 2)` to `server.py`
- [ ] Return: connected memories, entity nodes, edge types
- [ ] Add to `tests/bench_mcp_tools.py`
- [ ] Commit: `feat: add tool_graph_query MCP tool`

#### Task 2.6 — Repository Ingestion Graph Wiring

- [ ] In `do_semantic_snapshot_repo()`: after extracting imports, store as `depends_on` graph edges
- [ ] Store file → entity `contains` edges for every extracted function/class
- [ ] Store snapshot → repo_component `generated_from` edges
- [ ] Run `tests/test_ingestion.py` → all pass
- [ ] Commit: `feat: wire repository ingestion into graph layer`

---

## Phase 3 — Agent Memory Primitives + Advanced Retrieval

**Estimated sessions:** 2

### Primitives to expose as MCP tools

| Primitive | Semantics |
|-----------|----------|
| `remember(content, type)` | Store episodic memory, extract entities, link to graph |
| `forget(id)` | Delete memory + cascade graph edges |
| `compress(ids[])` | Merge N memories into one CompressedMemory, preserve source links |
| `reflect(query)` | Generate ReflectionMemory: agent reasons about what it knows |
| `learn(content)` | Store SemanticMemory with higher confidence weight |
| `link(id_a, id_b, edge_type)` | Create explicit graph edge between two memories |
| `snapshot()` | Take repo snapshot, diff from prior snapshot |
| `recall(query, strategy)` | Hybrid retrieval with explicit strategy: vector / bm25 / graph / temporal |

### Additional Retrieval Modes

- **Temporal retrieval:** `WHERE created_at BETWEEN ? AND ?` + vector rerank
- **Memory-chain retrieval:** follow `evolves_into` / `related_to` edges from a seed memory
- **Repository-aware retrieval:** filter by `repo_component` tags, traverse `depends_on` graph

### Tier System Fix

Replace positional tier promotion with frequency-based promotion:

```sql
ALTER TABLE memories ADD COLUMN recall_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_recalled TEXT;
```

- `hot`: recalled in last 7 days OR recall_count > 10
- `warm`: recalled in last 30 days OR recall_count > 3
- `cold`: not recalled in 30+ days AND recall_count <= 3

---

## Phase 4 — HNSW Migration (Performance)

**Estimated sessions:** 1

Replace `faiss.IndexFlatIP` with `faiss.IndexHNSWFlat` for O(log n) approximate search.

Key consideration: HNSW doesn't support `remove_ids`. Deletion strategy options:
1. Soft-delete in SQLite + periodic rebuild
2. Use `IndexIDMap2` wrapper which supports removal

Benchmark target: query latency < 10ms at 100K vectors (currently ~35ms with IndexFlatIP).

---

## Known Technical Debt to Address (Any Phase)

| Issue | File | Fix |
|-------|------|-----|
| `embed()` called inside `_lock` | `vector_store.py` | Pre-compute embedding, then acquire lock |
| N SQLite round-trips per query | `vector_store.py` | Batch `WHERE uuid IN (...)` fetch |
| Domain classification dead code | `context_builder.py` | Remove or fix (Phase 1 BM25 replaces need) |
| Tier has no behavioral consequence | `lifecycle.py` | Phase 3 tier fix |
| No input size guard before LLM | `ingest_tools.py` | Add `_LLM_MAX_INPUT_CHARS` guard |

---

## Benchmark Targets by Phase

| Phase | Benchmark | Target |
|-------|-----------|--------|
| Phase 1 | `bench_retrieval` — exact-term recall | > 0.85 (currently ~0.70 for identifiers) |
| Phase 1 | Query latency p99 | < 50ms at 10K memories |
| Phase 2 | Graph traversal latency | < 10ms for 2-hop at 10K nodes |
| Phase 2 | `bench_retrieval` — relationship queries | > 0.80 |
| Phase 4 | Query latency p99 | < 10ms at 100K vectors |

---

## Current Benchmark Baseline (v0.1.4)

- Security: 100/100
- MCP Tool Integrity: 100/100
- Performance: 100/100
- Retrieval: ~90/100 (semantic) / lower on exact-term queries
- Compression: ~85/100
- Query latency: stable through 100K memories with IndexFlatIP

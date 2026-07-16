# Memory Graph (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SMOS a relational knowledge layer — a SQLite-backed node/edge graph over memories and the entities they mention — so retrieval can traverse "auth → JWT → middleware → auth_service" instead of only ranking by text similarity.

**Architecture:** Every stored memory becomes a `memory` node. Entities extracted from its content (Python functions/classes/imports via `ast`, plus backtick-wrapped terms via regex — this project's own memory-file convention) become `entity` nodes, linked to the memory by a `references` edge. A bounded recursive-CTE traversal (`GraphStore.traverse`) answers "what's connected to X within N hops." `context_builder.py` uses this additively — it appends graph-linked memories the RRF ranking missed, without touching the existing ranked order (same "purely additive, zero regression risk" principle Phase 1 used for BM25). A standalone `tool_graph_query` MCP tool exposes direct traversal for explicit "what connects to X" queries.

**Tech Stack:** Python 3.10+, stdlib `ast` + `re` + `sqlite3` (recursive CTE), Pydantic v2. No new runtime dependency — this phase is stdlib-only per the roadmap's Phase 2 constraint.

## Global Constraints

(Copied verbatim from `docs/superpowers/plans/2026-06-30-smos-roadmap.md`; every task's requirements implicitly include these.)

- All existing benchmarks must pass after every phase: `bench_retrieval`, `bench_security`, `bench_mcp_tools`, `bench_performance`, `bench_compression`
- All existing MCP tools must remain functional and backward-compatible
- No new required runtime dependencies beyond `rank_bm25` (Phase 1, already added) and stdlib only for Phase 2
- SQLite schema changes via `CREATE TABLE IF NOT EXISTS` only — no destructive migrations
- Existing `.smos/` data directories must load without manual intervention
- Windows-compatible paths throughout (use `pathlib.Path`)
- Python 3.10+

**Verified-against-current-tree note (read this before trusting any snippet in this plan):** every signature below was read directly from the current files on 2026-07-16 (`smos/memory/vector_store.py`, `smos/memory/schemas.py`, `smos/server.py`, `smos/tools/semantic_tools.py`, `tests/conftest.py`, `tests/test_bm25_index.py`, `tests/test_hybrid_retrieval.py`, `tests/test_vector_store.py`) — not copied from the high-level roadmap sketch. Phase 1's plan drifted from the real tree on every one of its 4 tasks (see `bm25-phase1-lessons` cross-session memory); if you're executing this plan in a session where time has passed since 2026-07-16, **re-read the file before editing it** rather than trusting a line number here.

**Known Phase-2 scope limit (accepted, not a bug):** the graph is populated only by new `store()`/`store_batch()` calls going forward. Memories already in an existing `.smos/` corpus are not retroactively parsed into the graph on startup (unlike BM25's `_seed_bm25()`, which does rebuild from all of SQLite on every init). A full-corpus graph backfill is out of scope for this phase — track it as a future opportunistic task if a corpus needs it.

---

### Task 1: Graph schemas

**Files:**
- Modify: `smos/memory/schemas.py`
- Test: `tests/test_graph_schemas.py`

**Interfaces:**
- Produces: `GraphNode(id: str, type: Literal[...], name: str, metadata: dict = {}, created_at: datetime)` and `GraphEdge(id: str, source_id: str, target_id: str, edge_type: Literal[...], weight: float = 1.0, metadata: dict = {}, created_at: datetime)` in `smos/memory/schemas.py`. `type`/`edge_type` enums match the `CHECK` constraints used by `GraphStore`'s SQL schema in Task 2 — keep them in sync.

- [ ] **Step 1: Write the failing test**

Create `tests/test_graph_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from smos.memory.schemas import GraphEdge, GraphNode


def test_graph_node_constructs_with_defaults():
    node = GraphNode(id="mem1", type="memory", name="mem1")
    assert node.id == "mem1"
    assert node.metadata == {}
    assert node.created_at is not None


def test_graph_node_rejects_invalid_type():
    with pytest.raises(ValidationError):
        GraphNode(id="x", type="not_a_real_type", name="x")


def test_graph_edge_constructs_with_defaults():
    edge = GraphEdge(source_id="mem1", target_id="entity:JWT", edge_type="references")
    assert edge.source_id == "mem1"
    assert edge.target_id == "entity:JWT"
    assert edge.weight == 1.0
    assert edge.id  # auto-generated

def test_graph_edge_rejects_invalid_edge_type():
    with pytest.raises(ValidationError):
        GraphEdge(source_id="a", target_id="b", edge_type="not_a_real_edge_type")


def test_graph_node_metadata_round_trips():
    node = GraphNode(id="e1", type="entity", name="JWT", metadata={"kind": "reference"})
    assert node.model_dump()["metadata"] == {"kind": "reference"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'GraphNode'` (the class doesn't exist yet).

- [ ] **Step 3: Add the models**

In `smos/memory/schemas.py`, the file currently reads in full:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MemoryObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # adr / log / doc / issue
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list)
    tier: str = Field(default="hot")  # hot / warm / cold


class CompressedContext(BaseModel):
    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    mode: Literal["abstractive", "extractive", "uncertain"] = "abstractive"


class FileReadResult(BaseModel):
    summary: Optional[str]
    id: Optional[str]
    source: Optional[str]
    error: Optional[str] = None


class FileWriteResult(BaseModel):
    success: bool
```

Append at the end of the file:

```python


class GraphNode(BaseModel):
    id: str
    type: Literal["entity", "concept", "memory", "repo_component", "snapshot", "agent"]
    name: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GraphEdge(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    target_id: str
    edge_type: Literal[
        "references", "depends_on", "related_to",
        "implements", "contains", "evolves_into", "generated_from",
    ]
    weight: float = 1.0
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_graph_schemas.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/schemas.py tests/test_graph_schemas.py
git commit -m "feat(graph): add GraphNode/GraphEdge schemas"
```

---

### Task 2: GraphStore — nodes, edges, traversal

**Files:**
- Create: `smos/memory/graph_store.py`
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Consumes: nothing new (plain `sqlite3.Connection`, stdlib only).
- Produces: `GraphStore` class:
  - `GraphStore(db: sqlite3.Connection)` — creates `graph_nodes`/`graph_edges` tables via `CREATE TABLE IF NOT EXISTS` on the given connection.
  - `.add_node(node_id: str, type: str, name: str, metadata: Optional[dict] = None) -> None`
  - `.add_edge(source_id: str, target_id: str, edge_type: str, weight: float = 1.0, metadata: Optional[dict] = None) -> None` — idempotent; a repeat `(source_id, target_id, edge_type)` triple is silently ignored (no duplicate edges).
  - `.get_node(node_id: str) -> Optional[dict]` → `{"id", "type", "name", "metadata", "created_at"}` or `None`.
  - `.remove_node(node_id: str) -> None` — deletes the node and every edge touching it (either direction). Does **not** cascade to other nodes those edges pointed at.
  - `.node_count() -> int`, `.edge_count() -> int`
  - `.traverse(start_id: str, max_hops: int = 2, edge_types: Optional[list[str]] = None) -> list[dict]` — BFS from `start_id` treating edges as undirected, returns nodes reachable within `max_hops` (excluding `start_id` itself) as `{"id", "type", "name", "metadata", "hop"}`, ordered by `hop` then `id`. Returns `[]` if `start_id` doesn't exist or `max_hops < 1`.

**Deviation from the roadmap's SQL sketch (intentional):** the roadmap's `graph_edges` schema included `REFERENCES graph_nodes(id) ON DELETE CASCADE` foreign keys. This plan omits them — nothing in this codebase's SQLite setup runs `PRAGMA foreign_keys=ON` (VectorStore's `_init_db` doesn't set it), so those clauses would be silently inert, not actually enforced. Cleanup is instead done explicitly in `remove_node()` (delete touching edges, then the node), which is correct regardless of the pragma and matches how `VectorStore.delete()` already does manual cleanup rather than relying on FK cascade. Also added a `UNIQUE(source_id, target_id, edge_type)` constraint (not in the roadmap sketch) so `add_edge` can be idempotent via `INSERT OR IGNORE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_store.py`:

```python
import sqlite3

import pytest

from smos.memory.graph_store import GraphStore


@pytest.fixture
def graph() -> GraphStore:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return GraphStore(conn)


def test_add_node_and_get_node(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    node = graph.get_node("mem1")
    assert node is not None
    assert node["type"] == "memory"
    assert node["name"] == "mem1"


def test_add_node_is_idempotent(graph: GraphStore) -> None:
    graph.add_node("e1", "entity", "JWT", metadata={"kind": "reference"})
    graph.add_node("e1", "entity", "JWT", metadata={"kind": "reference"})
    assert graph.node_count() == 1


def test_get_node_returns_none_for_missing(graph: GraphStore) -> None:
    assert graph.get_node("nope") is None


def test_add_edge_and_count(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("e1", "entity", "JWT")
    graph.add_edge("mem1", "e1", "references")
    assert graph.edge_count() == 1


def test_add_edge_dedupes_same_triple(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("e1", "entity", "JWT")
    graph.add_edge("mem1", "e1", "references")
    graph.add_edge("mem1", "e1", "references")
    assert graph.edge_count() == 1


def test_remove_node_deletes_touching_edges_but_not_other_nodes(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("e1", "entity", "JWT")
    graph.add_edge("mem1", "e1", "references")

    graph.remove_node("mem1")

    assert graph.get_node("mem1") is None
    assert graph.edge_count() == 0
    assert graph.get_node("e1") is not None


def test_metadata_round_trips_as_dict(graph: GraphStore) -> None:
    graph.add_node("e1", "entity", "JWT", metadata={"kind": "reference"})
    node = graph.get_node("e1")
    assert node["metadata"] == {"kind": "reference"}


def test_traverse_finds_two_hop_neighbor(graph: GraphStore) -> None:
    # mem1 --references--> JWT <--references-- mem2
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("mem2", "memory", "mem2")
    graph.add_node("jwt", "entity", "JWT")
    graph.add_edge("mem1", "jwt", "references")
    graph.add_edge("mem2", "jwt", "references")

    result = graph.traverse("mem1", max_hops=2)
    ids = {n["id"] for n in result}
    assert "jwt" in ids
    assert "mem2" in ids
    jwt_hop = next(n["hop"] for n in result if n["id"] == "jwt")
    mem2_hop = next(n["hop"] for n in result if n["id"] == "mem2")
    assert jwt_hop == 1
    assert mem2_hop == 2


def test_traverse_respects_max_hops(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("mem2", "memory", "mem2")
    graph.add_node("jwt", "entity", "JWT")
    graph.add_edge("mem1", "jwt", "references")
    graph.add_edge("mem2", "jwt", "references")

    result = graph.traverse("mem1", max_hops=1)
    ids = {n["id"] for n in result}
    assert ids == {"jwt"}


def test_traverse_unknown_start_returns_empty(graph: GraphStore) -> None:
    assert graph.traverse("nope", max_hops=2) == []


def test_traverse_filters_by_edge_type(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("a", "entity", "A")
    graph.add_node("b", "concept", "B")
    graph.add_edge("mem1", "a", "references")
    graph.add_edge("mem1", "b", "related_to")

    result = graph.traverse("mem1", max_hops=1, edge_types=["references"])
    ids = {n["id"] for n in result}
    assert ids == {"a"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.memory.graph_store'`

- [ ] **Step 3: Write the implementation**

Create `smos/memory/graph_store.py`:

```python
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional


class GraphStore:
    """Thread-safe node/edge graph layer over a shared SQLite connection.

    Shares the caller's `sqlite3.Connection` (same pattern VectorStore uses
    for `memories`/`verbatim`/`ingested_files`) rather than opening its own
    database file. Every public method holds its own internal lock, mirroring
    BM25Index — callers do not need to hold any outer lock to use GraphStore
    safely on its own, though VectorStore does call it from within its own
    `_lock` anyway (see Task 4).
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id         TEXT PRIMARY KEY,
                    type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent')),
                    name       TEXT NOT NULL,
                    metadata   TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                    source_id  TEXT NOT NULL,
                    target_id  TEXT NOT NULL,
                    edge_type  TEXT NOT NULL CHECK(edge_type IN (
                                   'references','depends_on','related_to',
                                   'implements','contains','evolves_into','generated_from'
                               )),
                    weight     REAL DEFAULT 1.0,
                    metadata   TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(source_id, target_id, edge_type)
                )
            """)
            self._db.commit()

    def add_node(
        self, node_id: str, type: str, name: str, metadata: Optional[dict] = None
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO graph_nodes (id, type, name, metadata) VALUES (?,?,?,?)",
                (node_id, type, name, json.dumps(metadata or {})),
            )
            self._db.commit()

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        weight: float = 1.0,
        metadata: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO graph_edges (source_id, target_id, edge_type, weight, metadata) "
                "VALUES (?,?,?,?,?)",
                (source_id, target_id, edge_type, weight, json.dumps(metadata or {})),
            )
            self._db.commit()

    def get_node(self, node_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, type, name, metadata, created_at FROM graph_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "type": row[1],
            "name": row[2],
            "metadata": json.loads(row[3]) if row[3] else {},
            "created_at": row[4],
        }

    def remove_node(self, node_id: str) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM graph_edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id),
            )
            self._db.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            self._db.commit()

    def node_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]

    def edge_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

    def traverse(
        self,
        start_id: str,
        max_hops: int = 2,
        edge_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """BFS from start_id, treating edges as undirected. Terminates because
        `hop` is strictly bounded by max_hops regardless of graph cycles."""
        if max_hops < 1:
            return []

        edge_filter_sql = ""
        params: list = [start_id, max_hops]
        if edge_types:
            placeholders = ",".join("?" for _ in edge_types)
            edge_filter_sql = f"AND e.edge_type IN ({placeholders})"
            params.extend(edge_types)

        query = f"""
            WITH RECURSIVE traversal(node_id, hop) AS (
                SELECT ?, 0
                UNION
                SELECT
                    CASE WHEN e.source_id = t.node_id THEN e.target_id ELSE e.source_id END,
                    t.hop + 1
                FROM graph_edges e
                JOIN traversal t
                  ON (e.source_id = t.node_id OR e.target_id = t.node_id)
                WHERE t.hop < ?
                {edge_filter_sql}
            )
            SELECT n.id, n.type, n.name, n.metadata, MIN(tr.hop) as hop
            FROM (SELECT node_id, hop FROM traversal WHERE hop > 0) tr
            JOIN graph_nodes n ON n.id = tr.node_id
            GROUP BY n.id
            ORDER BY hop, n.id
        """
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "name": r[2],
                "metadata": json.loads(r[3]) if r[3] else {},
                "hop": r[4],
            }
            for r in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph_store.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/graph_store.py tests/test_graph_store.py
git commit -m "feat(graph): add GraphStore with node/edge CRUD and bounded traversal"
```

---

### Task 3: Entity extractor

**Files:**
- Create: `smos/memory/entity_extractor.py`
- Test: `tests/test_entity_extractor.py`

**Interfaces:**
- Consumes: nothing (stdlib `ast` + `re` only).
- Produces: `Entity` (`NamedTuple` with fields `name: str`, `kind: str` — `kind` is one of `"function"`, `"class"`, `"import"`, `"reference"`) and `extract_entities(content: str) -> list[Entity]` in `smos/memory/entity_extractor.py`. Deduplicates by `(kind, name)`, caps output at 50 entities per call.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_entity_extractor.py`:

```python
from smos.memory.entity_extractor import extract_entities


def test_extracts_function_and_class_from_python_source():
    content = """
class JWTValidator:
    def validate(self, token):
        return True

def helper():
    pass
"""
    entities = extract_entities(content)
    names = {(e.kind, e.name) for e in entities}
    assert ("class", "JWTValidator") in names
    assert ("function", "validate") in names
    assert ("function", "helper") in names


def test_extracts_import_targets():
    content = "import os\nfrom smos.memory.vector_store import VectorStore\n"
    entities = extract_entities(content)
    names = {(e.kind, e.name) for e in entities}
    assert ("import", "os") in names
    assert ("import", "smos") in names


def test_extracts_backtick_references_from_prose():
    content = "The `embed` function is called by `VectorStore.store`."
    entities = extract_entities(content)
    names = {e.name for e in entities if e.kind == "reference"}
    assert "embed" in names
    assert "VectorStore.store" in names


def test_backtick_regex_does_not_capture_trailing_parens():
    """Scope limit, not a bug: `foo()`-style spans (with parens) aren't
    captured — the char class stops at `(`. Entities are bare
    identifiers/dotted paths, matching this repo's own memory-file style."""
    content = "See `embed()` for details."
    entities = extract_entities(content)
    assert all(e.name != "embed()" for e in entities)


def test_non_python_content_yields_no_ast_entities_but_keeps_backticks():
    content = "This is prose with no code. It mentions `SomeEntity` though."
    entities = extract_entities(content)
    assert all(e.kind not in ("function", "class") for e in entities)
    assert any(e.name == "SomeEntity" for e in entities)


def test_deduplicates_repeated_mentions():
    content = "`JWT` appears here. `JWT` appears again. `JWT` a third time."
    entities = extract_entities(content)
    jwt_count = sum(1 for e in entities if e.name == "JWT")
    assert jwt_count == 1


def test_caps_at_fifty_entities():
    content = " ".join(f"`entity_{i}`" for i in range(100))
    entities = extract_entities(content)
    assert len(entities) <= 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_entity_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.memory.entity_extractor'`

- [ ] **Step 3: Write the implementation**

Create `smos/memory/entity_extractor.py`:

```python
from __future__ import annotations

import ast
import re
from typing import NamedTuple

_BACKTICK_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{1,60})`")
_MAX_ENTITIES = 50


class Entity(NamedTuple):
    name: str
    kind: str  # "function" | "class" | "import" | "reference"


def extract_entities(content: str) -> list[Entity]:
    """Extract code/concept entities from memory content.

    Two independent, best-effort sources:
      - AST walk (functions, classes, top-level import targets) — only
        fires when content parses as valid Python; yields nothing otherwise.
      - Backtick-wrapped spans (`` `like_this` ``) — works on any content,
        matching this project's own memory-file convention of naming code
        symbols in backticks.

    Deduplicates by (kind, name); caps output at 50 entities per call.
    """
    entities: list[Entity] = []
    seen: set[tuple[str, str]] = set()

    def _add(name: str, kind: str) -> None:
        key = (kind, name)
        if key in seen or len(entities) >= _MAX_ENTITIES:
            return
        seen.add(key)
        entities.append(Entity(name=name, kind=kind))

    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _add(node.name, "function")
            elif isinstance(node, ast.ClassDef):
                _add(node.name, "class")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    _add(alias.name.split(".")[0], "import")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    _add(node.module.split(".")[0], "import")
    except SyntaxError:
        pass

    for m in _BACKTICK_RE.finditer(content):
        _add(m.group(1), "reference")

    return entities
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_entity_extractor.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/entity_extractor.py tests/test_entity_extractor.py
git commit -m "feat(graph): add entity_extractor (AST + backtick-span extraction)"
```

---

### Task 4: Wire the graph into VectorStore

**Files:**
- Modify: `smos/memory/vector_store.py`
- Test: `tests/test_vector_store_graph.py`

**Interfaces:**
- Consumes: `GraphStore` (Task 2), `extract_entities` (Task 3).
- Produces: every `VectorStore` instance gets a `.graph: GraphStore` attribute, populated automatically by `store()` and `store_batch()`, cleaned up by `delete()` and `delete_batch()`. No public method signatures change.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vector_store_graph.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow())


def test_store_creates_memory_node(store: VectorStore) -> None:
    memory = _mem("The `JWTValidator` class handles tokens.")
    store.store(memory)
    node = store.graph.get_node(memory.id)
    assert node is not None
    assert node["type"] == "memory"


def test_store_creates_entity_nodes_and_edges(store: VectorStore) -> None:
    memory = _mem("The `JWTValidator` class handles tokens.")
    store.store(memory)
    entity_node = store.graph.get_node("entity:JWTValidator")
    assert entity_node is not None
    assert entity_node["type"] == "entity"
    neighbors = store.graph.traverse(memory.id, max_hops=1)
    ids = {n["id"] for n in neighbors}
    assert "entity:JWTValidator" in ids


def test_shared_entity_links_two_memories(store: VectorStore) -> None:
    m1 = _mem("The `JWTValidator` class validates tokens.")
    m2 = _mem("`JWTValidator` is used by the auth middleware.")
    store.store(m1)
    store.store(m2)
    neighbors = store.graph.traverse(m1.id, max_hops=2)
    ids = {n["id"] for n in neighbors}
    assert m2.id in ids


def test_delete_removes_memory_node(store: VectorStore) -> None:
    memory = _mem("The `JWTValidator` class handles tokens.")
    store.store(memory)
    store.delete(memory.id)
    assert store.graph.get_node(memory.id) is None


def test_store_batch_wires_graph_for_every_item(store: VectorStore) -> None:
    memories = [_mem(f"The `Entity{i}` does things.") for i in range(3)]
    store.store_batch(memories)
    for i, m in enumerate(memories):
        assert store.graph.get_node(m.id) is not None
        assert store.graph.get_node(f"entity:Entity{i}") is not None


def test_delete_batch_removes_memory_nodes(store: VectorStore) -> None:
    memories = [_mem(f"The `Entity{i}` does things.") for i in range(3)]
    store.store_batch(memories)
    store.delete_batch([m.id for m in memories])
    for m in memories:
        assert store.graph.get_node(m.id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vector_store_graph.py -v`
Expected: FAIL — `AttributeError: 'VectorStore' object has no attribute 'graph'`

- [ ] **Step 3: Wire GraphStore into VectorStore**

In `smos/memory/vector_store.py`, the import block currently reads:

```python
from smos.memory.bm25_index import BM25Index
from smos.memory.embeddings import embed, embed_batch
from smos.memory.schemas import MemoryObject
```

Replace with:

```python
from smos.memory.bm25_index import BM25Index
from smos.memory.embeddings import embed, embed_batch
from smos.memory.entity_extractor import extract_entities
from smos.memory.graph_store import GraphStore
from smos.memory.schemas import MemoryObject
```

`__init__` currently ends with:

```python
        self._index: faiss.IndexIDMap = self._load_or_create_index()
        self._db: sqlite3.Connection = self._init_db()
        self._rebuild_index_if_stale()
        self.bm25 = BM25Index()
        self._seed_bm25()
        atexit.register(self._atexit_save)
```

Replace with:

```python
        self._index: faiss.IndexIDMap = self._load_or_create_index()
        self._db: sqlite3.Connection = self._init_db()
        self._rebuild_index_if_stale()
        self.bm25 = BM25Index()
        self._seed_bm25()
        self.graph = GraphStore(self._db)
        atexit.register(self._atexit_save)
```

Add a new private method right after `_seed_bm25`. Current:

```python
    def _seed_bm25(self) -> None:
        """Build BM25 index from all memories in SQLite."""
        rows = self._db.execute(
            "SELECT uuid, content FROM memories ORDER BY row_id"
        ).fetchall()
        self.bm25.build([(r[0], r[1]) for r in rows])

    def _save_index(self) -> None:
```

Replace with:

```python
    def _seed_bm25(self) -> None:
        """Build BM25 index from all memories in SQLite."""
        rows = self._db.execute(
            "SELECT uuid, content FROM memories ORDER BY row_id"
        ).fetchall()
        self.bm25.build([(r[0], r[1]) for r in rows])

    def _update_graph(self, memory_id: str, content: str) -> None:
        """Extract entities from content and wire them into the graph.

        Best-effort, additive only. Does not seed from pre-existing memories
        on startup — only new store()/store_batch() calls populate the graph
        (see docs/HANDOFF.md for the backfill caveat).
        """
        self.graph.add_node(memory_id, "memory", memory_id[:8])
        for entity in extract_entities(content):
            entity_id = f"entity:{entity.name}"
            self.graph.add_node(entity_id, "entity", entity.name, metadata={"kind": entity.kind})
            self.graph.add_edge(memory_id, entity_id, "references")

    def _save_index(self) -> None:
```

In `store()`, currently:

```python
            self._index.add_with_ids(vector, np.array([row_id], dtype=np.int64))
            self.bm25.update(memory.id, memory.content)
            self._insert_count += 1
```

Replace with:

```python
            self._index.add_with_ids(vector, np.array([row_id], dtype=np.int64))
            self.bm25.update(memory.id, memory.content)
            self._update_graph(memory.id, memory.content)
            self._insert_count += 1
```

In `delete()`, currently:

```python
            self._db.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
            self._db.commit()
            self.bm25.remove(uuid)
            self._save_index()
```

Replace with:

```python
            self._db.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
            self._db.commit()
            self.bm25.remove(uuid)
            self.graph.remove_node(uuid)
            self._save_index()
```

In `delete_batch()`, currently:

```python
                row_ids.append(int(row[0]))
                self._db.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
            self.bm25.remove_batch(uuids)
            if row_ids:
```

Replace with:

```python
                row_ids.append(int(row[0]))
                self._db.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
            self.bm25.remove_batch(uuids)
            for uuid_to_unlink in uuids:
                self.graph.remove_node(uuid_to_unlink)
            if row_ids:
```

In `store_batch()`, currently:

```python
                id_arr = np.array(row_ids, dtype=np.int64)
                self._index.add_with_ids(arr, id_arr)
                self.bm25.add_batch([(m.id, m.content) for m in chunk])
                self._insert_count += len(chunk)
```

Replace with:

```python
                id_arr = np.array(row_ids, dtype=np.int64)
                self._index.add_with_ids(arr, id_arr)
                self.bm25.add_batch([(m.id, m.content) for m in chunk])
                for memory in chunk:
                    self._update_graph(memory.id, memory.content)
                self._insert_count += len(chunk)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vector_store_graph.py -v`
Expected: PASS (6 passed)

Then run the full suite to confirm nothing else broke:

Run: `pytest tests/ -v --tb=short`
Expected: all tests pass (no regressions in `test_vector_store.py`, `test_hybrid_retrieval.py`, `test_exact_term_recall.py`, etc.)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/vector_store.py tests/test_vector_store_graph.py
git commit -m "feat(graph): wire GraphStore into VectorStore store/delete paths"
```

---

### Task 5: Graph expansion in retrieval

**Files:**
- Modify: `smos/compression/context_builder.py`
- Modify: `tests/test_hybrid_retrieval.py` (existing `MagicMock`-based store helper needs a default stub — see Step 3b)
- Test: `tests/test_graph_expansion.py`

**Interfaces:**
- Consumes: `store.graph.traverse(...)` (Task 2/4), `store.get_by_uuid(...)` (existing).
- Produces: no signature change to `build_compressed_context(query, k, store, tags=None)` — the graph-expansion behavior is internal. `CompressedContext.sources` may now contain up to 2 extra memory ids beyond the RRF-ranked top-`k`, appended (not reordered) after the existing ranking logic.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_expansion.py`:

```python
from unittest.mock import MagicMock

from smos.compression.context_builder import build_compressed_context
from smos.memory.bm25_index import BM25Index


def _store_with(memories: list[dict], graph_neighbors: list[dict]):
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories])
    store.bm25 = bm25
    store.graph.traverse.return_value = graph_neighbors
    all_records = {m["id"]: m for m in memories}
    for n in graph_neighbors:
        if n["type"] == "memory" and "record" in n:
            all_records[n["id"]] = n["record"]
    store.get_by_uuid.side_effect = lambda uid: all_records.get(uid)
    return store


def test_graph_expansion_adds_linked_memory_not_found_by_rrf():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    linked_record = {"id": "id2", "content": "completely different wording, linked via graph", "metadata": {"tags": []}}
    graph_neighbors = [
        {"id": "id2", "type": "memory", "name": "id2", "metadata": {}, "hop": 2, "record": linked_record},
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert "id2" in result.sources


def test_graph_expansion_skips_non_memory_nodes():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    graph_neighbors = [
        {"id": "entity:JWTValidator", "type": "entity", "name": "JWTValidator", "metadata": {}, "hop": 1},
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert "entity:JWTValidator" not in result.sources


def test_graph_expansion_skips_nodes_already_in_top():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    graph_neighbors = [
        {"id": "id1", "type": "memory", "name": "id1", "metadata": {}, "hop": 1},
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert result.sources.count("id1") == 1


def test_graph_expansion_caps_at_two_extra_memories():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    graph_neighbors = [
        {
            "id": f"id{i}", "type": "memory", "name": f"id{i}", "metadata": {}, "hop": 2,
            "record": {"id": f"id{i}", "content": f"linked memory {i}", "metadata": {"tags": []}},
        }
        for i in range(2, 6)  # 4 candidates offered, only 2 should be added
    ]
    store = _store_with([top_result], graph_neighbors)

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert len(result.sources) <= 3  # 1 RRF result + at most 2 graph extras


def test_no_graph_neighbors_leaves_result_unchanged():
    top_result = {"id": "id1", "content": "JWTValidator class handles tokens", "distance": 0.1, "tags": []}
    store = _store_with([top_result], [])

    result = build_compressed_context("JWTValidator", k=1, store=store)

    assert result.sources == ["id1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph_expansion.py -v`
Expected: FAIL — `store.graph.traverse` is an unconfigured `MagicMock` attribute access, but `build_compressed_context` doesn't call it yet, so these tests fail on the *assertions* (e.g. `"id2" in result.sources` is `False`) rather than an error. Confirm each assertion fails before proceeding.

- [ ] **Step 3a: Add the graph-expansion step**

In `smos/compression/context_builder.py`, currently:

```python
    for c in candidates:
        c["_rrf_score"] = rrf_scores.get(c.get("id", ""), 0.0)
        c["_final_score"] = _term_overlap(query, c.get("content", ""))
    candidates.sort(key=lambda c: (c["_rrf_score"], c["_final_score"]), reverse=True)
    top = candidates[:k]

    # compress_memories_full() needs a "distance" per memory to derive
```

Replace with:

```python
    for c in candidates:
        c["_rrf_score"] = rrf_scores.get(c.get("id", ""), 0.0)
        c["_final_score"] = _term_overlap(query, c.get("content", ""))
    candidates.sort(key=lambda c: (c["_rrf_score"], c["_final_score"]), reverse=True)
    top = candidates[:k]

    # Graph expansion — purely additive: pulls in memories that share an
    # extracted entity with the top-ranked result, without altering the
    # RRF-ranked order of `top` itself. Bounded to 2 extra memories so a
    # densely-connected entity can't blow up the compression input.
    if top:
        graph_neighbors = store.graph.traverse(top[0].get("id", ""), max_hops=2, edge_types=["references"])
        existing_ids = {c.get("id", "") for c in top}
        added = 0
        for node in graph_neighbors:
            if added >= 2:
                break
            if node["type"] != "memory" or node["id"] in existing_ids:
                continue
            record = store.get_by_uuid(node["id"])
            if record is None:
                continue
            top.append(record)
            existing_ids.add(node["id"])
            added += 1

    # compress_memories_full() needs a "distance" per memory to derive
```

- [ ] **Step 3b: Stub `store.graph.traverse` in the existing hybrid-retrieval test helper**

The graph-expansion code now calls `store.graph.traverse(...)` unconditionally whenever `top` is non-empty. `tests/test_hybrid_retrieval.py`'s `_make_store_with_memories` builds a bare `MagicMock()` store, so without a default stub, every existing test in that file would break (iterating an unconfigured `MagicMock().graph.traverse(...)` return value raises `TypeError: object is not iterable`).

In `tests/test_hybrid_retrieval.py`, currently:

```python
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories + extra_bm25_docs])
    store.bm25 = bm25
```

Replace with:

```python
    store = MagicMock()
    store.query_domain.return_value = memories
    bm25 = BM25Index()
    bm25.build([(m["id"], m["content"]) for m in memories + extra_bm25_docs])
    store.bm25 = bm25
    store.graph.traverse.return_value = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph_expansion.py tests/test_hybrid_retrieval.py -v`
Expected: all pass (5 + existing hybrid-retrieval tests).

Then run the exact-term recall gate and the full suite — these use a **real** `VectorStore`/`GraphStore`, not mocks, so this is the real regression check:

Run: `pytest tests/test_exact_term_recall.py tests/ -v --tb=short`
Expected: all pass, including `test_exact_term_retrieval_quality` (recall ≥ 0.80) and `test_hybrid_recall_beats_vector_only`. These should be unaffected — the recall-gate corpus sentences contain no backticks and aren't valid Python, so `extract_entities` yields nothing for them, the graph stays empty for that corpus, and `traverse()` returns `[]`, making the expansion step a no-op for that test.

- [ ] **Step 5: Commit**

```bash
git add smos/compression/context_builder.py tests/test_hybrid_retrieval.py tests/test_graph_expansion.py
git commit -m "feat(graph): additive graph-expansion step in hybrid retrieval"
```

---

### Task 6: `tool_graph_query` MCP tool

**Files:**
- Modify: `smos/tools/semantic_tools.py`
- Modify: `smos/server.py`
- Test: `tests/test_graph_query_tool.py`

**Interfaces:**
- Consumes: `store.graph.get_node(...)`, `store.graph.traverse(...)` (Task 2/4).
- Produces: `graph_query(term: str, store: VectorStore, max_hops: int = 2) -> dict` in `smos/tools/semantic_tools.py`, returning `{"term", "start_id", "nodes": [{"id","type","name","metadata","hop"}], "count"}` (or `_INVALID_INPUT_RESPONSE` for a blank term). `tool_graph_query(term: str, max_hops: int = 2) -> dict` MCP tool in `smos/server.py`, following the existing `@app.tool()` try/except pattern used by every other tool in that file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_query_tool.py`:

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.semantic_tools import graph_query


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow())


def test_graph_query_resolves_bare_term_to_entity_node(store: VectorStore) -> None:
    store.store(_mem("The `JWT` token is validated by the middleware."))
    result = graph_query("JWT", store)
    assert result["start_id"] == "entity:JWT"
    assert result["count"] >= 1


def test_graph_query_finds_memory_via_entity(store: VectorStore) -> None:
    m = _mem("The `JWT` token is validated by the middleware.")
    memory_id = store.store(m)
    result = graph_query("JWT", store, max_hops=1)
    ids = {n["id"] for n in result["nodes"]}
    assert memory_id in ids


def test_graph_query_unknown_term_returns_empty(store: VectorStore) -> None:
    result = graph_query("NoSuchTermAnywhere", store)
    assert result["nodes"] == []
    assert result["count"] == 0


def test_graph_query_empty_term_is_invalid(store: VectorStore) -> None:
    result = graph_query("   ", store)
    assert result.get("status") == "skipped"


def test_graph_query_accepts_exact_memory_id(store: VectorStore) -> None:
    m = _mem("The `JWT` token is validated by the middleware.")
    memory_id = store.store(m)
    result = graph_query(memory_id, store)
    assert result["start_id"] == memory_id
    ids = {n["id"] for n in result["nodes"]}
    assert "entity:JWT" in ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph_query_tool.py -v`
Expected: FAIL with `ImportError: cannot import name 'graph_query'`

- [ ] **Step 3: Add `graph_query` and wire the MCP tool**

In `smos/tools/semantic_tools.py`, currently ends with:

```python
def semantic_write(
    type: str,
    content: str,
    store: VectorStore,
    tags: list[str] | None = None,
) -> str:
    if not _validate_text(content):
        return ""
    memory = MemoryObject(
        type=type,
        content=content,
        timestamp=datetime.utcnow(),
        tags=tags or [],
    )
    return store.store(memory)
```

Append:

```python


def graph_query(term: str, store: VectorStore, max_hops: int = 2) -> dict:
    """Traverse the memory graph starting from an entity name or exact node id.

    `term` is deliberately not passed through `_validate_text` (its 10-char
    minimum is tuned for prose content, not short lookup terms like "JWT").
    """
    term = term.strip()
    if not term:
        return _INVALID_INPUT_RESPONSE
    start_id = term if store.graph.get_node(term) else f"entity:{term}"
    nodes = store.graph.traverse(start_id, max_hops=max_hops)
    nodes.sort(key=lambda n: n["hop"])
    return {"term": term, "start_id": start_id, "nodes": nodes, "count": len(nodes)}
```

In `smos/server.py`, currently:

```python
from smos.tools.semantic_tools import (
    _MIN_INPUT_CHARS,
    _INVALID_INPUT_RESPONSE,
    semantic_query,
    semantic_store,
    semantic_write,
)
```

Replace with:

```python
from smos.tools.semantic_tools import (
    _MIN_INPUT_CHARS,
    _INVALID_INPUT_RESPONSE,
    graph_query,
    semantic_query,
    semantic_store,
    semantic_write,
)
```

Add a new tool after `tool_semantic_write` (i.e. right before the `@app.tool()` for `tool_read_file_compress`). Currently:

```python
@app.tool()
def tool_read_file_compress(path: str) -> dict:
```

Replace with:

```python
@app.tool()
def tool_graph_query(term: str, max_hops: int = 2) -> dict:
    """Traverse the memory graph starting from an entity or memory id.

    Finds nodes connected to `term` within `max_hops` — e.g. term="JWT" finds
    every memory that references JWT (1 hop) and other entities those
    memories also reference (2 hops), transitively. If `term` isn't an
    existing node id, it's resolved as `entity:{term}` automatically.

    Returns {"term", "start_id", "nodes": [{"id","type","name","metadata","hop"}], "count"}.

    Args:
        term: Entity name (e.g. "JWT") or an exact node id.
        max_hops: Traversal depth (default 2).
    """
    try:
        return graph_query(term, _store, max_hops=max_hops)
    except Exception as exc:
        return {
            "term": term, "start_id": None, "nodes": [], "count": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


@app.tool()
def tool_read_file_compress(path: str) -> dict:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph_query_tool.py -v`
Expected: PASS (5 passed)

Then confirm the server still imports cleanly (catches any typo in the new import/tool block):

Run: `python -c "from smos import server"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add smos/tools/semantic_tools.py smos/server.py tests/test_graph_query_tool.py
git commit -m "feat(graph): expose tool_graph_query MCP tool"
```

---

### Task 7: Graph retrieval benchmark (non-gating)

**Files:**
- Create: `tests/bench_graph_retrieval.py`

**Interfaces:**
- Consumes: `VectorStore` (with `.graph`), nothing else. Standalone script — no other task depends on this one.
- Produces: nothing consumed elsewhere. `pytest.ini` restricts collection to `test_*.py` (see `tests/test_exact_term_recall.py`'s docstring for why — this exact mistake already burned Phase 1 once), so this file is **not** pytest-collected. Run it directly: `python tests/bench_graph_retrieval.py`.

- [ ] **Step 1: Write the benchmark script**

Create `tests/bench_graph_retrieval.py`:

```python
"""
bench_graph_retrieval.py — Memory Graph traversal benchmark.

Standalone script (NOT pytest-collected — see tests/test_exact_term_recall.py's
docstring for why files in tests/ named bench_*.py aren't picked up by `pytest
tests/`). Run directly: `python tests/bench_graph_retrieval.py`

Seeds a small corpus of memories that share entities across a chain
(auth -> JWT -> middleware -> auth_service, mirroring the roadmap's own
example), then times traversal at increasing hop depth and reports how many
memories become reachable at each depth versus what plain vector/BM25
retrieval would surface for the same query.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

_CHAIN_DOCS = [
    "The `auth_service` validates incoming requests before routing them.",
    "`auth_service` delegates token checks to the `middleware` layer.",
    "The `middleware` layer parses the `JWT` from the Authorization header.",
    "`JWT` tokens are signed and verified using the `auth` module's secret key.",
    "The `auth` module also handles password hashing and session rotation.",
]

_UNRELATED_DOCS = [
    "PostgreSQL connection pooling reduces per-request handshake overhead.",
    "Kubernetes rolling updates minimize downtime during deploys.",
    "FAISS IndexFlatIP performs brute-force cosine similarity search.",
]


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="smos_bench_graph_"))
    try:
        store = VectorStore(persist_path=tmp_dir / "data")

        ids = []
        for doc in _CHAIN_DOCS + _UNRELATED_DOCS:
            ids.append(store.store(MemoryObject(type="doc", content=doc)))

        start_id = "entity:auth_service"
        print(f"Graph size: {store.graph.node_count()} nodes, {store.graph.edge_count()} edges")
        print(f"Traversing from {start_id!r}:\n")

        for hops in (1, 2, 3, 4):
            t0 = time.perf_counter()
            neighbors = store.graph.traverse(start_id, max_hops=hops)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            memory_hits = [n for n in neighbors if n["type"] == "memory"]
            entity_hits = [n for n in neighbors if n["type"] == "entity"]
            print(
                f"  max_hops={hops}: {len(memory_hits)} memories, "
                f"{len(entity_hits)} entities reached in {elapsed_ms:.2f}ms"
            )

        vector_results = store.query("auth service", k=5)
        print(f"\nFor comparison — plain vector query 'auth service' (k=5) "
              f"surfaced {len(vector_results)} results without graph traversal.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and eyeball the output**

Run: `python tests/bench_graph_retrieval.py`
Expected: prints graph size, then increasing memory/entity counts as `max_hops` grows from 1 to 4 (the 5-doc chain should become fully connected by hop 3–4), with no exceptions. No pass/fail assertion — this is informational, matching the existing `bench_*.py` convention.

- [ ] **Step 3: Commit**

```bash
git add tests/bench_graph_retrieval.py
git commit -m "test: add non-gating memory graph traversal benchmark"
```

---

## After all tasks: update the handoff

Per the project workflow (`CLAUDE.md`), update `docs/HANDOFF.md` at the end of the session: mark Phase 2 complete with the merge commit, note the graph-backfill limitation is still open, and point to Phase 3 (Agent Memory Primitives) as next.

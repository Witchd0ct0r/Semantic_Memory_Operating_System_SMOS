# Phase 5.1 — Foundation: Schema & Adapter Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the additive schema and adapter-interface foundation that every later Phase 5 sub-phase (segmentation, ingestion contract, two-stage retrieval, traceability) builds on: a `document` graph-node type, an open/adapter-extensible edge-type vocabulary, segment reconstruction metadata (`doc_id`/`ordinal`/`char_start`/`char_end`) on the `memories` table, a pluggable `DomainAdapter` interface with one minimal concrete adapter, and a dedup-lifecycle carve-out for ingested segments.

**Architecture:** Every DB change is additive — either an `ALTER TABLE ADD COLUMN` (mirrors the existing `tier`/`source_ids`/`confidence` precedent in `vector_store.py`) or a widened `CHECK` constraint applied via a one-time table-rebuild migration (SQLite cannot `ALTER` a `CHECK` constraint in place, so pre-Phase-5 databases are detected via `sqlite_master.sql` and rebuilt losslessly). The adapter interface is a new `smos/ingest/` package, isolated from existing code; only `agent_primitives.link()` changes to consume it.

**Tech Stack:** Python, SQLite (stdlib `sqlite3`), Pydantic v2 (`smos/memory/schemas.py`), pytest.

## Global Constraints

- **No new required runtime dependencies** — this sub-phase adds zero third-party packages (stdlib `re`, `sqlite3`, `typing`, `threading` only), consistent with the Phase 5 decision to keep multilingual/format deps out of SMOS core.
- **Additive schema only** — no destructive migrations. Column additions use the existing `try: ALTER TABLE ... ADD COLUMN ...; except sqlite3.OperationalError: pass` pattern. `CHECK`-constraint widening rebuilds the table (rename → create → copy → drop) and must preserve every existing row.
- **Test collection** — `pytest.ini` sets `python_files = ["test_*.py"]`. All new tests go in `test_*.py` files under `tests/`; never `bench_*.py` (those are standalone scripts, not pytest-collected).
- **Run tests:** `pytest tests/ -v --tb=short`. If you see an unexpected **"No tests collected"** on this machine, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken (known `rtk` hook quirk, see `docs/HANDOFF.md`).
- **Style:** `from __future__ import annotations` at the top of every new/modified module; builtin generic type hints (`list[str]`, `dict`, `Optional[...]`); no comments except where genuinely non-obvious (match the style already in every file this plan touches).
- **Scope fence:** this plan is sub-phase 5.1 ("Foundation") only. Do **not** implement segmentation (Pillar B), the ingestion contract (Pillar G), retrieval changes (Pillar D), traceability fan-out (Pillar F), or multilingual embeddings (§9) here — those are separate, later sub-phase plans (5.2 onward per `docs/HANDOFF.md`).

---

### Task 1: `document` graph-node type

**Files:**
- Modify: `smos/memory/schemas.py:39-44` (`GraphNode.type` Literal)
- Modify: `smos/memory/graph_store.py:25-51` (`_init_schema`, new `_migrate_legacy_graph_nodes_check`)
- Test: `tests/test_graph_schemas.py`
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Consumes: nothing new — `GraphStore.__init__(db: sqlite3.Connection)`, `add_node(node_id, type, name, metadata=None)`, `get_node(node_id)` (all unchanged signatures, from `smos/memory/graph_store.py:20-93`).
- Produces: `GraphNode(type=...)` and `GraphStore.add_node(..., type="document", ...)` both accept `"document"` without raising. Later sub-phases (5.2/5.3) will create one `document` node per ingested document.

- [ ] **Step 1: Write the failing schema test**

Add to `tests/test_graph_schemas.py`:

```python
def test_graph_node_accepts_document_type():
    node = GraphNode(id="doc1", type="document", name="doc1")
    assert node.type == "document"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_graph_schemas.py::test_graph_node_accepts_document_type -v`
Expected: FAIL with `pydantic_core._pydantic_core.ValidationError` (`"document"` not a permitted Literal value).

- [ ] **Step 3: Widen the GraphNode Literal**

In `smos/memory/schemas.py`, change the `GraphNode.type` field (currently line 41):

```python
class GraphNode(BaseModel):
    id: str
    type: Literal["entity", "concept", "memory", "repo_component", "snapshot", "agent", "document"]
    name: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_graph_schemas.py::test_graph_node_accepts_document_type -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add smos/memory/schemas.py tests/test_graph_schemas.py
git commit -m "feat: allow document type on GraphNode schema"
```

- [ ] **Step 6: Write the failing GraphStore tests**

Add to `tests/test_graph_store.py` (the file already imports `sqlite3` and `GraphStore`, and has a `graph` fixture):

```python
def test_add_node_document_type_succeeds(graph: GraphStore) -> None:
    graph.add_node("doc1", "document", "doc1")
    node = graph.get_node("doc1")
    assert node is not None
    assert node["type"] == "document"


def test_migrates_legacy_graph_nodes_check_constraint(tmp_path) -> None:
    db_path = tmp_path / "legacy_nodes.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('mem1', 'memory', 'mem1', '{}')"
    )
    conn.commit()

    store = GraphStore(conn)

    old_node = store.get_node("mem1")
    assert old_node is not None
    assert old_node["type"] == "memory"

    store.add_node("doc1", "document", "doc1")
    new_node = store.get_node("doc1")
    assert new_node is not None
    assert new_node["type"] == "document"
```

- [ ] **Step 7: Run them to verify they fail**

Run: `pytest tests/test_graph_store.py -v --tb=short -k "document_type or migrates_legacy_graph_nodes"`
Expected: FAIL — both raise `sqlite3.IntegrityError: CHECK constraint failed: graph_nodes` on the `add_node("doc1", "document", ...)` call.

- [ ] **Step 8: Implement the CHECK widening and legacy migration**

In `smos/memory/graph_store.py`, replace `_init_schema` (currently lines 25-51) with:

```python
    def _init_schema(self) -> None:
        with self._lock:
            self._migrate_legacy_graph_nodes_check()
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id         TEXT PRIMARY KEY,
                    type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document')),
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

    def _migrate_legacy_graph_nodes_check(self) -> None:
        """Rebuild graph_nodes if it exists with the pre-Phase-5 CHECK constraint
        (no 'document' type). SQLite has no ALTER TABLE for CHECK constraints, so
        this copies existing rows into a freshly-constrained table. Only called
        from _init_schema, which already holds self._lock — no locking here.
        """
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_nodes'"
        ).fetchone()
        if row is None or row[0] is None or "'document'" in row[0]:
            return  # table doesn't exist yet, or already migrated
        self._db.execute("ALTER TABLE graph_nodes RENAME TO graph_nodes_legacy")
        self._db.execute("""
            CREATE TABLE graph_nodes (
                id         TEXT PRIMARY KEY,
                type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document')),
                name       TEXT NOT NULL,
                metadata   TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._db.execute(
            "INSERT INTO graph_nodes (id, type, name, metadata, created_at) "
            "SELECT id, type, name, metadata, created_at FROM graph_nodes_legacy"
        )
        self._db.execute("DROP TABLE graph_nodes_legacy")
        self._db.commit()
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/test_graph_store.py tests/test_graph_schemas.py -v --tb=short`
Expected: PASS (all tests in both files, including the two new ones).

- [ ] **Step 10: Commit**

```bash
git add smos/memory/graph_store.py tests/test_graph_store.py
git commit -m "feat: add document node type with legacy-schema migration"
```

---

### Task 2: Adapter interface skeleton (Pillar A)

**Files:**
- Create: `smos/ingest/__init__.py`
- Create: `smos/ingest/adapters/__init__.py`
- Create: `smos/ingest/adapters/base.py`
- Create: `smos/ingest/adapters/registry.py`
- Create: `smos/ingest/adapters/markdown.py`
- Test: `tests/test_adapter_registry.py`
- Test: `tests/test_markdown_adapter.py`

**Interfaces:**
- Produces: `DomainAdapter` Protocol (`smos.ingest.adapters.base`) with `name: str`, `edge_types: frozenset[str]`, `structural_boundaries(text: str) -> list[tuple[int, int]]`, `extract_references(text: str) -> list[tuple[str, str, str]]`, `llm_boundary_prompt() -> Optional[str]`. `register_adapter(adapter)`, `get_adapter(name) -> Optional[DomainAdapter]`, `all_edge_types() -> frozenset[str]`, `reset_registry()` (test-only) from `smos.ingest.adapters.registry`. `MarkdownAdapter` (`smos.ingest.adapters.markdown`) as the first concrete adapter, `name = "markdown"`, `edge_types = frozenset()`. Task 3 consumes `all_edge_types()`.

- [ ] **Step 1: Create the package skeleton**

`smos/ingest/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the base adapter interface**

`smos/ingest/adapters/base.py`:

```python
from __future__ import annotations

from typing import Optional, Protocol


class DomainAdapter(Protocol):
    """Pluggable domain-specific knowledge for ingestion and the memory graph.

    The segmenter, extractor, storage, and retrieval layers stay
    domain-agnostic; an adapter supplies only what's specific to one
    domain's documents. A generic adapter (no structure detected, no
    references extracted) is a valid, if unhelpful, implementation — every
    method degrades gracefully to "nothing domain-specific here".
    """

    name: str
    edge_types: frozenset[str]

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        """(start_char, end_char) spans for this domain's native structural
        units (headings, articles, clauses...). Empty list means no
        domain-specific structure was detected; the caller falls through to
        the segmenter's semantic/size-fit layers."""
        ...

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        """(source_name, target_name, edge_type) triples for this domain's
        cross-references. Every edge_type returned must be a member of
        self.edge_types."""
        ...

    def llm_boundary_prompt(self) -> Optional[str]:
        """Prompt guiding LLM-proposed segment boundaries (segmenter's
        opt-in Layer 4), or None if this adapter doesn't need it."""
        ...
```

- [ ] **Step 3: Write the failing registry tests**

`tests/test_adapter_registry.py`:

```python
from __future__ import annotations

import pytest

from smos.ingest.adapters.registry import (
    all_edge_types,
    get_adapter,
    register_adapter,
    reset_registry,
)


class _FakeAdapter:
    name = "fake"
    edge_types = frozenset({"satisfies", "verified_by"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self):
        return None


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_all_edge_types_includes_core_vocabulary_with_no_adapters_registered():
    types = all_edge_types()
    assert "references" in types
    assert "depends_on" in types
    assert "contains" in types


def test_register_adapter_extends_edge_vocabulary():
    register_adapter(_FakeAdapter())
    types = all_edge_types()
    assert "satisfies" in types
    assert "verified_by" in types
    assert "references" in types


def test_get_adapter_returns_registered_instance():
    adapter = _FakeAdapter()
    register_adapter(adapter)
    assert get_adapter("fake") is adapter


def test_get_adapter_returns_none_for_unknown_name():
    assert get_adapter("does-not-exist") is None
```

- [ ] **Step 4: Run it to verify it fails**

Run: `pytest tests/test_adapter_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.adapters.registry'`.

- [ ] **Step 5: Implement the registry**

`smos/ingest/adapters/registry.py`:

```python
from __future__ import annotations

from typing import Optional

from smos.ingest.adapters.base import DomainAdapter

_CORE_EDGE_TYPES: frozenset[str] = frozenset({
    "references", "depends_on", "related_to",
    "implements", "contains", "evolves_into", "generated_from",
})

_adapters: dict[str, DomainAdapter] = {}


def register_adapter(adapter: DomainAdapter) -> None:
    _adapters[adapter.name] = adapter


def get_adapter(name: str) -> Optional[DomainAdapter]:
    return _adapters.get(name)


def all_edge_types() -> frozenset[str]:
    """Core Phase 2/3 edge vocabulary, unioned with every registered
    adapter's domain-specific edge types (Pillar F traceability edges)."""
    types = set(_CORE_EDGE_TYPES)
    for adapter in _adapters.values():
        types |= set(adapter.edge_types)
    return frozenset(types)


def reset_registry() -> None:
    """Test-only: clear registered adapters between test cases."""
    _adapters.clear()
```

- [ ] **Step 6: Run it to verify it passes**

Run: `pytest tests/test_adapter_registry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add smos/ingest/__init__.py smos/ingest/adapters/base.py smos/ingest/adapters/registry.py tests/test_adapter_registry.py
git commit -m "feat: add adapter-extensible edge-vocabulary registry"
```

- [ ] **Step 8: Write the failing markdown-adapter tests**

`tests/test_markdown_adapter.py`:

```python
from __future__ import annotations

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.adapters.registry import all_edge_types, get_adapter, register_adapter, reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_structural_boundaries_splits_on_headings():
    adapter = MarkdownAdapter()
    text = "# Title\nIntro text.\n## Section A\nBody A.\n## Section B\nBody B.\n"
    spans = adapter.structural_boundaries(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].startswith("# Title")
    assert text[spans[1][0]:spans[1][1]].startswith("## Section A")
    assert text[spans[2][0]:spans[2][1]].startswith("## Section B")


def test_structural_boundaries_empty_for_no_headings():
    adapter = MarkdownAdapter()
    assert adapter.structural_boundaries("Just plain text, no headings.") == []


def test_extract_references_returns_empty_for_now():
    adapter = MarkdownAdapter()
    assert adapter.extract_references("# Title\nSome text with `no refs`.") == []


def test_llm_boundary_prompt_is_none():
    assert MarkdownAdapter().llm_boundary_prompt() is None


def test_registering_markdown_adapter_does_not_change_core_vocabulary():
    register_adapter(MarkdownAdapter())
    assert all_edge_types() == {
        "references", "depends_on", "related_to",
        "implements", "contains", "evolves_into", "generated_from",
    }


def test_get_adapter_finds_explicitly_registered_markdown_adapter():
    register_adapter(MarkdownAdapter())
    adapter = get_adapter("markdown")
    assert adapter is not None
    assert adapter.name == "markdown"
```

- [ ] **Step 9: Run it to verify it fails**

Run: `pytest tests/test_markdown_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.adapters.markdown'`.

- [ ] **Step 10: Implement the markdown adapter and package init**

`smos/ingest/adapters/markdown.py`:

```python
from __future__ import annotations

import re
from typing import Optional

_HEADING_RE = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


class MarkdownAdapter:
    """Minimal domain adapter proving the DomainAdapter interface end-to-end.

    Structural grammar: split on markdown ATX headings (# through ######).
    No cross-reference extraction or LLM boundary prompt yet — those land
    with later, domain-specific adapters (Pillar F).
    """

    name = "markdown"
    edge_types: frozenset[str] = frozenset()

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return []
        spans = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            spans.append((start, end))
        return spans

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
```

`smos/ingest/adapters/__init__.py`:

```python
from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.adapters.registry import register_adapter

register_adapter(MarkdownAdapter())
```

- [ ] **Step 11: Run it to verify it passes**

Run: `pytest tests/test_markdown_adapter.py tests/test_adapter_registry.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 12: Commit**

```bash
git add smos/ingest/adapters/markdown.py smos/ingest/adapters/__init__.py tests/test_markdown_adapter.py
git commit -m "feat: add minimal markdown domain adapter"
```

---

### Task 3: Open-text, adapter-extensible edge vocabulary

**Files:**
- Modify: `smos/memory/schemas.py:47-58` (`GraphEdge.edge_type`)
- Modify: `smos/memory/graph_store.py` (`_init_schema`, new `_migrate_legacy_graph_edges_check`)
- Modify: `smos/tools/agent_primitives.py:1-14,41-47` (`link()`)
- Modify: `tests/test_graph_schemas.py:27-29` (replace obsolete rejection test)
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Consumes: `all_edge_types()` from `smos.ingest.adapters.registry` (Task 2).
- Produces: `GraphEdge(edge_type=...)` accepts any string. `GraphStore.add_edge(source_id, target_id, edge_type, ...)` no longer DB-rejects unknown edge types (validation moved to `agent_primitives.link()` and, later, ingestion call sites).

- [ ] **Step 1: Write the failing schema test**

In `tests/test_graph_schemas.py`, replace the existing `test_graph_edge_rejects_invalid_edge_type` (lines 27-29) with:

```python
def test_graph_edge_accepts_open_text_edge_type():
    """edge_type validity is enforced in application code against the
    adapter-extensible vocabulary (smos/ingest/adapters/registry.py), not by
    this schema — Phase 5 decision, see docs/superpowers/specs/2026-07-17-*."""
    edge = GraphEdge(source_id="a", target_id="b", edge_type="satisfies")
    assert edge.edge_type == "satisfies"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_graph_schemas.py::test_graph_edge_accepts_open_text_edge_type -v`
Expected: FAIL with `pydantic_core._pydantic_core.ValidationError` (`"satisfies"` not a permitted Literal value).

- [ ] **Step 3: Open up the GraphEdge schema**

In `smos/memory/schemas.py`, change `GraphEdge` (currently lines 47-58):

```python
class GraphEdge(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    target_id: str
    edge_type: str
    weight: float = 1.0
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_graph_schemas.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/schemas.py tests/test_graph_schemas.py
git commit -m "feat: open GraphEdge.edge_type to adapter-extensible vocabulary"
```

- [ ] **Step 6: Write the failing GraphStore tests**

Add to `tests/test_graph_store.py`:

```python
def test_add_edge_accepts_open_text_edge_type(graph: GraphStore) -> None:
    graph.add_node("mem1", "memory", "mem1")
    graph.add_node("req1", "entity", "REQ-1")
    graph.add_edge("mem1", "req1", "satisfies")
    assert graph.edge_count() == 1


def test_migrates_legacy_graph_edges_check_constraint(tmp_path) -> None:
    db_path = tmp_path / "legacy_edges.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE graph_edges (
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
    conn.execute("INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('mem1','memory','mem1','{}')")
    conn.execute("INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('req1','entity','REQ-1','{}')")
    conn.execute(
        "INSERT INTO graph_edges (source_id, target_id, edge_type) VALUES ('mem1','req1','references')"
    )
    conn.commit()

    store = GraphStore(conn)

    assert store.edge_count() == 1
    store.add_edge("mem1", "req1", "satisfies")
    assert store.edge_count() == 2
```

- [ ] **Step 7: Run them to verify they fail**

Run: `pytest tests/test_graph_store.py -v --tb=short -k "open_text_edge_type or migrates_legacy_graph_edges"`
Expected: FAIL — `sqlite3.IntegrityError: CHECK constraint failed: graph_edges` on the `add_edge(..., "satisfies")` calls.

- [ ] **Step 8: Implement the CHECK removal and legacy migration**

In `smos/memory/graph_store.py`, update `_init_schema` to also call the new edge migration and drop the edge CHECK:

```python
    def _init_schema(self) -> None:
        with self._lock:
            self._migrate_legacy_graph_nodes_check()
            self._migrate_legacy_graph_edges_check()
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id         TEXT PRIMARY KEY,
                    type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document')),
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
                    edge_type  TEXT NOT NULL,
                    weight     REAL DEFAULT 1.0,
                    metadata   TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(source_id, target_id, edge_type)
                )
            """)
            self._db.commit()
```

Add the new migration method (alongside `_migrate_legacy_graph_nodes_check`):

```python
    def _migrate_legacy_graph_edges_check(self) -> None:
        """Rebuild graph_edges if it exists with the pre-Phase-5 CHECK constraint
        that restricted edge_type to a fixed vocabulary. Phase 5 makes edge_type
        open TEXT; validity is enforced in application code against the
        adapter-extensible vocabulary (smos/ingest/adapters/registry.py). Only
        called from _init_schema, which already holds self._lock.
        """
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_edges'"
        ).fetchone()
        if row is None or row[0] is None or "CHECK(edge_type" not in row[0]:
            return  # table doesn't exist yet, or already migrated
        self._db.execute("ALTER TABLE graph_edges RENAME TO graph_edges_legacy")
        self._db.execute("""
            CREATE TABLE graph_edges (
                id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                source_id  TEXT NOT NULL,
                target_id  TEXT NOT NULL,
                edge_type  TEXT NOT NULL,
                weight     REAL DEFAULT 1.0,
                metadata   TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(source_id, target_id, edge_type)
            )
        """)
        self._db.execute(
            "INSERT INTO graph_edges (id, source_id, target_id, edge_type, weight, metadata, created_at) "
            "SELECT id, source_id, target_id, edge_type, weight, metadata, created_at FROM graph_edges_legacy"
        )
        self._db.execute("DROP TABLE graph_edges_legacy")
        self._db.commit()
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/test_graph_store.py -v --tb=short`
Expected: PASS (all tests in the file)

- [ ] **Step 10: Commit**

```bash
git add smos/memory/graph_store.py tests/test_graph_store.py
git commit -m "feat: make graph_edges.edge_type open text with legacy-schema migration"
```

- [ ] **Step 11: Wire agent_primitives.link() to the adapter registry**

In `smos/tools/agent_primitives.py`, remove the module-level `_VALID_EDGE_TYPES` constant (currently lines 11-14) and add the registry import (near the top, alongside the existing imports):

```python
from smos.ingest.adapters.registry import all_edge_types
```

Replace `link()` (currently lines 41-47):

```python
def link(id_a: str, id_b: str, edge_type: str, store: VectorStore) -> dict:
    if edge_type not in all_edge_types():
        return {"success": False, "error": f"invalid edge_type: {edge_type}"}
    if store.graph.get_node(id_a) is None or store.graph.get_node(id_b) is None:
        return {"success": False, "error": "one or both ids do not exist as graph nodes"}
    store.graph.add_edge(id_a, id_b, edge_type)
    return {"success": True, "error": None}
```

- [ ] **Step 12: Run the existing agent_primitives tests to confirm no regression**

Run: `pytest tests/test_agent_primitives.py -v --tb=short`
Expected: PASS (all existing tests, including `test_link_rejects_invalid_edge_type` and `test_link_adds_edge_for_valid_nodes` — unchanged, now backed by the registry instead of the hardcoded set).

- [ ] **Step 13: Commit**

```bash
git add smos/tools/agent_primitives.py
git commit -m "refactor: validate link() edge_type against the adapter registry"
```

---

### Task 4: Segment reconstruction metadata on `memories`

**Files:**
- Modify: `smos/memory/schemas.py:10-18` (`MemoryObject`)
- Modify: `smos/memory/vector_store.py:67-118` (`_init_db`), `:176-212` (`store`), `:370-391` (`get_by_uuid`), `:433-494` (`store_batch`)
- Test: `tests/test_vector_store_segment_columns.py`

**Interfaces:**
- Produces: `MemoryObject(doc_id: Optional[str] = None, ordinal: Optional[int] = None, char_start: Optional[int] = None, char_end: Optional[int] = None)`. `VectorStore.get_by_uuid(uuid)["metadata"]` gains keys `doc_id`, `ordinal`, `char_start`, `char_end`. Consumed by the segmentation (5.2) and reconstruction (5.4) sub-phases.

- [ ] **Step 1: Write the failing test**

`tests/test_vector_store_segment_columns.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_store_persists_segment_metadata(store: VectorStore) -> None:
    memory = MemoryObject(
        type="segment",
        content="Article 9(2)(b) permits processing with explicit consent.",
        doc_id="doc-1",
        ordinal=3,
        char_start=120,
        char_end=180,
    )
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["doc_id"] == "doc-1"
    assert record["metadata"]["ordinal"] == 3
    assert record["metadata"]["char_start"] == 120
    assert record["metadata"]["char_end"] == 180


def test_store_defaults_segment_metadata_to_none(store: VectorStore) -> None:
    memory = MemoryObject(type="doc", content="Plain content with no segment metadata.")
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["doc_id"] is None
    assert record["metadata"]["ordinal"] is None
    assert record["metadata"]["char_start"] is None
    assert record["metadata"]["char_end"] is None


def test_store_batch_persists_segment_metadata(store: VectorStore) -> None:
    memories = [
        MemoryObject(
            type="segment",
            content="Batch segment content long enough to embed.",
            doc_id="doc-2",
            ordinal=0,
            char_start=0,
            char_end=44,
        )
    ]
    ids = store.store_batch(memories)
    record = store.get_by_uuid(ids[0])
    assert record["metadata"]["doc_id"] == "doc-2"
    assert record["metadata"]["ordinal"] == 0
    assert record["metadata"]["char_start"] == 0
    assert record["metadata"]["char_end"] == 44
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_vector_store_segment_columns.py -v`
Expected: FAIL with `pydantic_core._pydantic_core.ValidationError: Extra inputs are not permitted` (`doc_id`/`ordinal`/`char_start`/`char_end` unknown to `MemoryObject`).

- [ ] **Step 3: Add the fields to MemoryObject**

In `smos/memory/schemas.py`, change `MemoryObject` (currently lines 10-18):

```python
class MemoryObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # adr / log / doc / issue / episodic / semantic / compressed / reflection / segment
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list)
    tier: str = Field(default="hot")  # hot / warm / cold
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    doc_id: Optional[str] = None
    ordinal: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
```

- [ ] **Step 4: Run it to verify it still fails (schema now accepts the fields, storage doesn't persist them yet)**

Run: `pytest tests/test_vector_store_segment_columns.py -v`
Expected: FAIL with `KeyError: 'doc_id'` (or similar) — `get_by_uuid`'s `metadata` dict doesn't have these keys yet.

- [ ] **Step 5: Add the columns and wire store()/store_batch()/get_by_uuid()**

In `smos/memory/vector_store.py`, add four more `ALTER TABLE` blocks in `_init_db` (right after the existing `last_recalled` block, currently ending at line 101, before the `verbatim` table creation at line 102):

```python
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN doc_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN ordinal INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN char_start INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN char_end INTEGER")
        except sqlite3.OperationalError:
            pass
```

Update the `INSERT` in `store()` (currently lines 181-194):

```python
            cursor = self._db.execute(
                "INSERT INTO memories (uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                "doc_id, ordinal, char_start, char_end) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    memory.id,
                    memory.type,
                    memory.content,
                    memory.timestamp.isoformat(),
                    ",".join(memory.tags),
                    memory.tier,
                    ",".join(memory.source_ids),
                    memory.confidence,
                    memory.doc_id,
                    memory.ordinal,
                    memory.char_start,
                    memory.char_end,
                ),
            )
```

Update the `INSERT` in `store_batch()` (currently lines 459-472):

```python
                    cursor = self._db.execute(
                        "INSERT INTO memories (uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                        "doc_id, ordinal, char_start, char_end) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            memory.id,
                            memory.type,
                            memory.content,
                            memory.timestamp.isoformat(),
                            ",".join(memory.tags),
                            memory.tier,
                            ",".join(memory.source_ids),
                            memory.confidence,
                            memory.doc_id,
                            memory.ordinal,
                            memory.char_start,
                            memory.char_end,
                        ),
                    )
```

Replace `get_by_uuid()` (currently lines 370-391):

```python
    def get_by_uuid(self, uuid: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT uuid, type, content, timestamp, tags, tier, source_ids, confidence, "
                "doc_id, ordinal, char_start, char_end "
                "FROM memories WHERE uuid = ?",
                (uuid,),
            ).fetchone()
        if row is None:
            return None
        (uuid_, type_, content, timestamp, tags, tier, source_ids, confidence,
         doc_id, ordinal, char_start, char_end) = row
        return {
            "id": uuid_,
            "content": content,
            "metadata": {
                "type": type_,
                "timestamp": timestamp,
                "tags": tags,
                "tier": tier,
                "source_ids": [s for s in source_ids.split(",") if s] if source_ids else [],
                "confidence": confidence,
                "doc_id": doc_id,
                "ordinal": ordinal,
                "char_start": char_start,
                "char_end": char_end,
            },
        }
```

- [ ] **Step 6: Run it to verify it passes**

Run: `pytest tests/test_vector_store_segment_columns.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run the full vector_store and phase3-columns suites to confirm no regression**

Run: `pytest tests/test_vector_store.py tests/test_vector_store_phase3_columns.py tests/test_vector_store_graph.py -v --tb=short`
Expected: PASS (all tests in all three files).

- [ ] **Step 8: Commit**

```bash
git add smos/memory/schemas.py smos/memory/vector_store.py tests/test_vector_store_segment_columns.py
git commit -m "feat: add doc_id/ordinal/char_start/char_end segment metadata columns"
```

---

### Task 5: Dedup-scoping exclusion for ingested segments

**Files:**
- Modify: `smos/memory/lifecycle.py:41-79` (`_deduplicate`)
- Test: `tests/test_lifecycle_dedup.py`

**Interfaces:**
- Consumes: `VectorStore.get_by_uuid(uuid)["metadata"]["type"]` (existing), `MemoryObject.type == "segment"` (the convention this task establishes — later sub-phases store ingested document segments with `type="segment"`).
- Produces: no signature change to `LifecycleManager._deduplicate()` — behavior only.

- [ ] **Step 1: Write the failing tests**

`tests/test_lifecycle_dedup.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.lifecycle import LifecycleManager
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_dedup_skips_ingested_segments(store: VectorStore) -> None:
    ids = [
        store.store(MemoryObject(type="segment", content="Article 9(2)(b) governs consent.")),
        store.store(MemoryObject(type="segment", content="Article 9(2)(b) governs consent.")),
    ]
    for i in range(10):
        store.store(MemoryObject(type="doc", content=f"Unrelated filler fact number {i}."))

    manager = LifecycleManager(store)
    manager._store._new_ids = list(ids)
    manager._deduplicate()

    for uuid in ids:
        assert store.get_by_uuid(uuid) is not None


def test_dedup_still_removes_non_segment_near_duplicates(store: VectorStore) -> None:
    ids = [
        store.store(MemoryObject(type="doc", content="The service uses Redis for caching sessions.")),
        store.store(MemoryObject(type="doc", content="The service uses Redis for caching sessions.")),
    ]
    for i in range(10):
        store.store(MemoryObject(type="doc", content=f"Unrelated filler fact number {i}."))

    manager = LifecycleManager(store)
    manager._store._new_ids = list(ids)
    manager._deduplicate()

    remaining = [uuid for uuid in ids if store.get_by_uuid(uuid) is not None]
    assert len(remaining) == 1
```

- [ ] **Step 2: Run them to verify the segment test fails**

Run: `pytest tests/test_lifecycle_dedup.py -v --tb=short`
Expected: `test_dedup_still_removes_non_segment_near_duplicates` PASSES (this is existing behavior); `test_dedup_skips_ingested_segments` FAILS — one of the two identical `"segment"` memories gets deleted by the current, type-blind dedup logic.

- [ ] **Step 3: Implement the type check**

In `smos/memory/lifecycle.py`, in `_deduplicate()` (currently lines 41-79), add the segment check right after the existing `record is None` guard:

```python
    def _deduplicate(self) -> None:
        """Incremental dedup: O(M) per cycle where M = inserts since last drain."""
        new_uuids = self._store._drain_new_ids()
        if len(new_uuids) < 2:
            return
        if self._store.count() < _MIN_STORE_SIZE:
            return

        new_set = set(new_uuids)
        processed: set[str] = set()
        to_delete: list[str] = []
        to_delete_set: set[str] = set()

        for uuid in new_uuids:
            if uuid in to_delete_set:
                processed.add(uuid)
                continue

            record = self._store.get_by_uuid(uuid)
            if record is None:
                processed.add(uuid)
                continue
            if record["metadata"]["type"] == "segment":
                processed.add(uuid)
                continue

            similar = self._store.query(record["content"], k=5)
            for s in similar:
                if s["distance"] >= _SIMILARITY_THRESHOLD:
                    continue
                sid = s["id"]
                if sid == uuid or sid in to_delete_set:
                    continue
                if sid not in new_set or sid in processed:
                    to_delete.append(uuid)
                    to_delete_set.add(uuid)
                    break

            processed.add(uuid)

        if to_delete:
            self._store.delete_batch(to_delete)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_lifecycle_dedup.py -v --tb=short`
Expected: PASS (both tests)

- [ ] **Step 5: Run the full lifecycle suite to confirm no regression**

Run: `pytest tests/test_lifecycle_tiers.py tests/test_lifecycle_dedup.py -v --tb=short`
Expected: PASS (all tests in both files)

- [ ] **Step 6: Commit**

```bash
git add smos/memory/lifecycle.py tests/test_lifecycle_dedup.py
git commit -m "fix: exclude ingested-document segments from lifecycle dedup"
```

---

## Final verification

- [ ] **Run the full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: PASS, count higher than the pre-plan 187 (5 new test files plus additions to 3 existing ones). If you see "No tests collected" unexpectedly, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken.

- [ ] **Update `docs/HANDOFF.md`**

Add a "Phase 5.1 — Foundation: COMPLETE" section (mirroring the existing Phase 1/2/3 entries) summarizing: `document` node type, open adapter-extensible edge vocabulary, segment metadata columns, the `DomainAdapter` interface + `MarkdownAdapter`, and the dedup-scoping fix. Note that 5.2 (segmentation cascade) is next, per `docs/superpowers/specs/2026-07-17-domain-general-ingestion-and-retrieval-design.md`.

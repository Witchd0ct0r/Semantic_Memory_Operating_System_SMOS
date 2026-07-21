# Phase 5.2 — Segmentation Cascade & Containment Tree Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `smos/ingest/segmenter.py`: a structural → semantic → size-fit cascade (Pillar B) that segments a document's raw text into non-overlapping leaf spans, and an orchestrator that stores each leaf as a searchable `segment` memory and wires a three-level containment tree (`document` → `section` → segment) into the memory graph via `contains` edges.

**Architecture:** The cascade is a pure, recursive function over `(text, start, end)` spans with no I/O — fully unit-testable without a `VectorStore`/`GraphStore`. A separate orchestrator function (`ingest_document_text`) consumes the resulting span tree, batch-stores leaves as `MemoryObject(type="segment", ...)` (which auto-creates `"memory"`-type graph nodes via `VectorStore._update_graph`, already wired since Phase 2), and wires `contains` edges. A `"section"` node type is added (mirroring 5.1's `"document"` addition) for structural groupings that had to be split further — a section that fits as a single leaf skips the section layer entirely and links directly under `document`, since a container with exactly one child adds no reconstruction value.

**Tech Stack:** Python, SQLite (stdlib `sqlite3`), Pydantic v2, NumPy (already a transitive dep via FAISS/sentence-transformers), pytest.

**Key design constraint driving the containment-tree shape:** reconstruction (Phase 5.4) concatenates leaf segments in `ordinal` order to reproduce the original document exactly — this only works if leaf spans are non-overlapping and no ancestor span is *also* separately stored as a memory (that would duplicate text on reconstruction). Therefore `section` nodes are pure graph containers with no embedding/FAISS/BM25 row of their own; only leaves are stored as memories.

## Global Constraints

- **No new required runtime dependencies** — `numpy` is already installed transitively (FAISS, sentence-transformers); no new packages. Consistent with the Phase 5 "zero new required deps" decision.
- **Additive schema only** — the `"section"` node-type CHECK widening follows the exact rename→create→copy→drop pattern `_migrate_legacy_graph_nodes_check` already uses for `"document"` in 5.1; must preserve every existing row, including 5.1-era rows that already have `type='document'`.
- **Containment depth capped at exactly three levels** (`document` → `section` → segment) — this is the Governor-resolved decision from spec §11. Even if the structural adapter nests multiple heading levels (e.g. markdown H2 containing H3), all leaves nested under a given top-level structural child attach directly to that one `section` node — no deeper section nesting.
- **Layer 4 (LLM-proposed boundaries) is out of scope for this sub-phase.** The spec (§5) marks it optional/opt-in ("hardest cases"); structural + semantic + size-fit already covers the deterministic 80-90% case and keeps this slice's tests fast and non-flaky (no live Ollama calls). Note this explicitly in the HANDOFF update as a deferred follow-up, not a gap that blocks 5.3/5.4.
- **Embedding-window proxy is character-based, not token-based** — `all-MiniLM-L6-v2` truncates at 256 tokens (verified: `SentenceTransformer('all-MiniLM-L6-v2').max_seq_length == 256`). Loading the tokenizer just to count tokens is unnecessary overhead for a segmentation size check; `_MAX_SEGMENT_CHARS = 800` (~200 tokens at a conservative ~4 chars/token for English prose) leaves comfortable margin under the 256-token truncation point.
- **Test collection** — `pytest.ini` sets `python_files = ["test_*.py"]`. New tests go in `test_*.py` files under `tests/`.
- **Run tests:** `pytest tests/ -v --tb=short`. If you see an unexpected **"No tests collected"**, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken (known `rtk` hook quirk, see `docs/HANDOFF.md`).
- **Style:** `from __future__ import annotations` at the top of every new/modified module; builtin generic type hints (`list[str]`, `dict`, `Optional[...]`); no comments except where genuinely non-obvious (match the style already in every file this plan touches).
- **Scope fence:** this plan is sub-phase 5.2 ("Segmentation cascade") only. Do **not** wire this into `smos/tools/ingest_tools.py`, the MCP tool surface (`smos/server.py`), or the ingestion-contract Pydantic schema — those are 5.3's job (`docs/superpowers/specs/2026-07-17-domain-general-ingestion-and-retrieval-design.md` §8.6, Pillar G). Do not touch `recall_engine.py`/reconstruction — that's 5.4.

---

### Task 1: `section` graph-node type

**Files:**
- Modify: `smos/memory/schemas.py:43-48` (`GraphNode.type` Literal)
- Modify: `smos/memory/graph_store.py:25-50` (`_init_schema`), `:52-78` (`_migrate_legacy_graph_nodes_check`)
- Test: `tests/test_graph_schemas.py`
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Consumes: nothing new — `GraphStore.__init__(db: sqlite3.Connection)`, `add_node(node_id, type, name, metadata=None)`, `get_node(node_id)` (unchanged signatures).
- Produces: `GraphNode(type="section")` and `GraphStore.add_node(..., "section", ...)` both accept `"section"` without raising. Consumed by Task 3's `_wire_containment`.

- [ ] **Step 1: Write the failing schema test**

Add to `tests/test_graph_schemas.py`:

```python
def test_graph_node_accepts_section_type():
    node = GraphNode(id="doc1:section:0", type="section", name="doc1:section:0")
    assert node.type == "section"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_graph_schemas.py::test_graph_node_accepts_section_type -v`
Expected: FAIL with `pydantic_core._pydantic_core.ValidationError` (`"section"` not a permitted Literal value).

- [ ] **Step 3: Widen the GraphNode Literal**

In `smos/memory/schemas.py`, change the `GraphNode.type` field (currently line 45):

```python
class GraphNode(BaseModel):
    id: str
    type: Literal["entity", "concept", "memory", "repo_component", "snapshot", "agent", "document", "section"]
    name: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_graph_schemas.py::test_graph_node_accepts_section_type -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add smos/memory/schemas.py tests/test_graph_schemas.py
git commit -m "feat: allow section type on GraphNode schema"
```

- [ ] **Step 6: Write the failing GraphStore tests**

Add to `tests/test_graph_store.py`:

```python
def test_add_node_section_type_succeeds(graph: GraphStore) -> None:
    graph.add_node("doc1:section:0", "section", "doc1:section:0")
    node = graph.get_node("doc1:section:0")
    assert node is not None
    assert node["type"] == "section"


def test_migrates_5_1_schema_graph_nodes_check_constraint(tmp_path) -> None:
    """A 5.1-era DB already has 'document' but not 'section' in its CHECK
    constraint — the migration guard must key off 'section', not 'document',
    or it will wrongly consider a 5.1 DB already migrated."""
    db_path = tmp_path / "phase5_1_nodes.db"
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
    conn.execute(
        "INSERT INTO graph_nodes (id, type, name, metadata) VALUES ('doc1', 'document', 'doc1', '{}')"
    )
    conn.commit()

    store = GraphStore(conn)

    old_node = store.get_node("doc1")
    assert old_node is not None
    assert old_node["type"] == "document"

    store.add_node("doc1:section:0", "section", "doc1:section:0")
    new_node = store.get_node("doc1:section:0")
    assert new_node is not None
    assert new_node["type"] == "section"
```

- [ ] **Step 7: Run them to verify they fail**

Run: `pytest tests/test_graph_store.py -v --tb=short -k "section_type or migrates_5_1_schema"`
Expected: FAIL — both raise `sqlite3.IntegrityError: CHECK constraint failed: graph_nodes` on the `add_node(..., "section", ...)` call.

- [ ] **Step 8: Implement the CHECK widening and migration-guard fix**

In `smos/memory/graph_store.py`, replace `_init_schema` (currently lines 25-50):

```python
    def _init_schema(self) -> None:
        with self._lock:
            self._migrate_legacy_graph_nodes_check()
            self._migrate_legacy_graph_edges_check()
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id         TEXT PRIMARY KEY,
                    type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document','section')),
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

Replace `_migrate_legacy_graph_nodes_check` (currently lines 52-78):

```python
    def _migrate_legacy_graph_nodes_check(self) -> None:
        """Rebuild graph_nodes if it exists with a CHECK constraint that
        predates the 'section' type (added Phase 5.2). Covers both pre-Phase-5
        DBs (no 'document', no 'section') and 5.1-era DBs (has 'document', not
        'section') — the guard keys off 'section' specifically so a 5.1 DB
        isn't mistaken for already-migrated. SQLite has no ALTER TABLE for
        CHECK constraints, so this copies existing rows into a freshly
        constrained table. Only called from _init_schema, which already holds
        self._lock — no locking here.
        """
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_nodes'"
        ).fetchone()
        if row is None or row[0] is None or "'section'" in row[0]:
            return  # table doesn't exist yet, or already migrated
        self._db.execute("ALTER TABLE graph_nodes RENAME TO graph_nodes_legacy")
        self._db.execute("""
            CREATE TABLE graph_nodes (
                id         TEXT PRIMARY KEY,
                type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document','section')),
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
git commit -m "feat: add section node type with 5.1-schema migration"
```

---

### Task 2: Segmentation cascade (pure, no I/O)

**Files:**
- Create: `smos/ingest/segmenter.py`
- Test: `tests/test_segmenter_cascade.py`

**Interfaces:**
- Consumes: `DomainAdapter.structural_boundaries(text: str) -> list[tuple[int, int]]` (`smos.ingest.adapters.base`, unchanged from 5.1), `embed(text: str) -> list[float]` (`smos.memory.embeddings`, unchanged).
- Produces: `Span` dataclass (`start: int`, `end: int`, `children: list[Span]`, `memory_id: Optional[str]`, `is_leaf` property). `_build_span_tree(full_text: str, start: int, end: int, adapter: DomainAdapter) -> Span`, `_merge_undersized(full_text: str, node: Span) -> None`, `_flatten_leaves(node: Span) -> list[Span]`. Task 3 consumes all four names directly (same module).

- [ ] **Step 1: Write the failing tests for the size/sentence/fixed-split helpers**

`tests/test_segmenter_cascade.py`:

```python
from __future__ import annotations

from smos.ingest.segmenter import (
    Span,
    _MAX_SEGMENT_CHARS,
    _MIN_SEGMENT_CHARS,
    _build_span_tree,
    _fits,
    _fixed_size_split,
    _flatten_leaves,
    _merge_undersized,
    _semantic_boundaries,
    _split_sentences,
)


class _NoStructureAdapter:
    name = "no-structure"
    edge_types: frozenset[str] = frozenset()

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self):
        return None


def test_fits_true_under_threshold():
    assert _fits("short text") is True


def test_fits_false_over_threshold():
    assert _fits("x" * (_MAX_SEGMENT_CHARS + 1)) is False


def test_split_sentences_returns_offsets_covering_text():
    text = "First sentence. Second sentence! Third sentence?"
    spans = _split_sentences(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].strip() == "First sentence."
    assert text[spans[1][0]:spans[1][1]].strip() == "Second sentence!"
    assert text[spans[2][0]:spans[2][1]].strip() == "Third sentence?"


def test_split_sentences_single_sentence():
    assert len(_split_sentences("Only one sentence here.")) == 1


def test_fixed_size_split_covers_text_without_gaps():
    text = "x" * 1500
    spans = _fixed_size_split(text)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for i in range(len(spans) - 1):
        assert spans[i][1] == spans[i + 1][0]


def test_fixed_size_split_short_text_returns_single_span():
    text = "short"
    assert _fixed_size_split(text) == [(0, len(text))]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_segmenter_cascade.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.segmenter'`.

- [ ] **Step 3: Implement the module through the fixed-size layer**

`smos/ingest/segmenter.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from smos.ingest.adapters.base import DomainAdapter
from smos.memory.embeddings import embed

# Character-based proxy for all-MiniLM-L6-v2's 256-token truncation window.
# ~4 chars/token for English prose, with margin — avoids loading the
# tokenizer just to count tokens during segmentation.
_MAX_SEGMENT_CHARS = 800
_MIN_SEGMENT_CHARS = 20
_FIXED_WINDOW_CHARS = 700
_SEMANTIC_VALLEY_THRESHOLD = 0.35

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Span:
    start: int
    end: int
    children: list["Span"] = field(default_factory=list)
    memory_id: Optional[str] = None

    @property
    def is_leaf(self) -> bool:
        return not self.children


def _fits(text: str) -> bool:
    return len(text) <= _MAX_SEGMENT_CHARS


def _split_sentences(text: str) -> list[tuple[int, int]]:
    bounds = [m.end() for m in _SENTENCE_BOUNDARY_RE.finditer(text)]
    starts = [0] + bounds
    ends = bounds + [len(text)]
    return [(s, e) for s, e in zip(starts, ends) if text[s:e].strip()]


def _fixed_size_split(text: str) -> list[tuple[int, int]]:
    spans = []
    n = len(text)
    pos = 0
    while pos < n:
        end = min(pos + _FIXED_WINDOW_CHARS, n)
        spans.append((pos, end))
        pos = end
    return spans or [(0, n)]
```

- [ ] **Step 4: Run it to verify the first batch passes**

Run: `pytest tests/test_segmenter_cascade.py -v -k "fits or split_sentences or fixed_size"`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add smos/ingest/segmenter.py tests/test_segmenter_cascade.py
git commit -m "feat: add segmenter size-fit and sentence-split helpers"
```

- [ ] **Step 6: Write the failing semantic-boundary and tree-building tests**

Append to `tests/test_segmenter_cascade.py`:

```python
def test_semantic_boundaries_splits_unrelated_sentences():
    text = "The cat sat quietly on the warm mat. Quantum entanglement violates local realism."
    chunks = _semantic_boundaries(text)
    assert len(chunks) == 2
    assert text[chunks[0][0]:chunks[0][1]] == text[: chunks[0][1]]
    assert chunks[-1][1] == len(text)


def test_semantic_boundaries_keeps_related_sentences_together():
    text = "The cat sat on the mat. The cat then sat on the rug."
    chunks = _semantic_boundaries(text)
    assert len(chunks) == 1


def test_semantic_boundaries_single_sentence_returns_one_chunk():
    chunks = _semantic_boundaries("Just one sentence, nothing to split.")
    assert len(chunks) == 1


def test_build_span_tree_uses_structural_boundaries_when_available():
    from smos.ingest.adapters.markdown import MarkdownAdapter

    text = "# Title\nIntro.\n## Section A\nBody A.\n## Section B\nBody B.\n"
    root = _build_span_tree(text, 0, len(text), MarkdownAdapter())
    assert len(root.children) == 3
    assert not root.is_leaf


def test_build_span_tree_leaf_when_no_structure_and_fits():
    text = "A short paragraph with no headings at all."
    root = _build_span_tree(text, 0, len(text), _NoStructureAdapter())
    assert root.is_leaf
    assert (root.start, root.end) == (0, len(text))


def test_build_span_tree_falls_back_to_size_fit_when_oversized_and_no_structure():
    text = "x" * (_MAX_SEGMENT_CHARS * 3)
    root = _build_span_tree(text, 0, len(text), _NoStructureAdapter())
    assert not root.is_leaf
    leaves = _flatten_leaves(root)
    assert leaves[0].start == 0
    assert leaves[-1].end == len(text)
    for i in range(len(leaves) - 1):
        assert leaves[i].end == leaves[i + 1].start


def test_merge_undersized_absorbs_tiny_fragment_into_neighbor():
    root = Span(start=0, end=100, children=[
        Span(start=0, end=5),
        Span(start=5, end=100),
    ])
    _merge_undersized("x" * 100, root)
    assert len(root.children) == 1
    assert root.children[0].start == 0
    assert root.children[0].end == 100


def test_flatten_leaves_returns_leaves_in_document_order():
    root = Span(start=0, end=10, children=[
        Span(start=0, end=4),
        Span(start=4, end=10, children=[
            Span(start=4, end=7),
            Span(start=7, end=10),
        ]),
    ])
    leaves = _flatten_leaves(root)
    assert [(s.start, s.end) for s in leaves] == [(0, 4), (4, 7), (7, 10)]
```

- [ ] **Step 7: Run it to verify it fails**

Run: `pytest tests/test_segmenter_cascade.py -v -k "semantic or build_span_tree or merge_undersized or flatten_leaves"`
Expected: FAIL with `ImportError` (`_semantic_boundaries`, `_build_span_tree`, `_merge_undersized`, `_flatten_leaves` not yet defined).

- [ ] **Step 8: Implement the semantic layer, tree builder, merge, and flatten functions**

Append to `smos/ingest/segmenter.py`:

```python
def _semantic_boundaries(text: str) -> list[tuple[int, int]]:
    sentences = _split_sentences(text)
    if len(sentences) < 2:
        return [(0, len(text))] if sentences else []

    vectors = [np.array(embed(text[s:e])) for s, e in sentences]
    sims = [float(np.dot(vectors[i], vectors[i + 1])) for i in range(len(vectors) - 1)]

    boundaries = [0]
    for i, sim in enumerate(sims):
        is_local_min = (i == 0 or sim <= sims[i - 1]) and (i == len(sims) - 1 or sim <= sims[i + 1])
        if is_local_min and sim < _SEMANTIC_VALLEY_THRESHOLD:
            boundaries.append(i + 1)
    boundaries.append(len(sentences))

    chunks = []
    for i in range(len(boundaries) - 1):
        chunk_sentences = sentences[boundaries[i]:boundaries[i + 1]]
        if not chunk_sentences:
            continue
        chunks.append((chunk_sentences[0][0], chunk_sentences[-1][1]))
    return chunks


def _build_span_tree(full_text: str, start: int, end: int, adapter: DomainAdapter) -> Span:
    span_text = full_text[start:end]
    node = Span(start=start, end=end)

    structural = adapter.structural_boundaries(span_text)
    if len(structural) > 1:
        for s, e in structural:
            node.children.append(_build_span_tree(full_text, start + s, start + e, adapter))
        return node

    if _fits(span_text):
        return node

    semantic = _semantic_boundaries(span_text)
    if len(semantic) > 1:
        for s, e in semantic:
            child_start, child_end = start + s, start + e
            child_text = full_text[child_start:child_end]
            if _fits(child_text):
                node.children.append(Span(start=child_start, end=child_end))
            else:
                for fs, fe in _fixed_size_split(child_text):
                    node.children.append(Span(start=child_start + fs, end=child_start + fe))
        return node

    for s, e in _fixed_size_split(span_text):
        node.children.append(Span(start=start + s, end=start + e))
    return node


def _merge_undersized(full_text: str, node: Span) -> None:
    for child in node.children:
        _merge_undersized(full_text, child)

    if len(node.children) < 2:
        return

    merged: list[Span] = []
    for child in node.children:
        text_len = len(full_text[child.start:child.end].strip())
        if text_len < _MIN_SEGMENT_CHARS and merged:
            prev = merged[-1]
            prev.end = child.end
            prev.children.extend(child.children)
        else:
            merged.append(child)
    node.children[:] = merged


def _flatten_leaves(node: Span) -> list[Span]:
    if node.is_leaf:
        return [node]
    leaves: list[Span] = []
    for child in node.children:
        leaves.extend(_flatten_leaves(child))
    return leaves
```

- [ ] **Step 9: Run the full cascade test file to verify it passes**

Run: `pytest tests/test_segmenter_cascade.py -v --tb=short`
Expected: PASS (all tests in the file, ~17 total)

- [ ] **Step 10: Commit**

```bash
git add smos/ingest/segmenter.py tests/test_segmenter_cascade.py
git commit -m "feat: add structural/semantic/size-fit segmentation cascade"
```

---

### Task 3: Containment-tree wiring & orchestrator

**Files:**
- Modify: `smos/ingest/segmenter.py` (append `_wire_containment` and `ingest_document_text`)
- Test: `tests/test_segmenter_containment.py`

**Interfaces:**
- Consumes: `Span`, `_build_span_tree`, `_merge_undersized`, `_flatten_leaves` (Task 2, same module). `GraphStore.add_node`/`add_edge` and `VectorStore.store_batch` (unchanged, from 5.1/pre-existing). `GraphNode(type="section")` (Task 1).
- Produces: `ingest_document_text(doc_id: str, text: str, store: VectorStore, adapter: DomainAdapter, doc_name: Optional[str] = None) -> dict` with keys `doc_id`, `segment_count`, `segment_ids`, `section_count`. This is the entry point Phase 5.3's `ingest_tools.py` integration will call.

- [ ] **Step 1: Write the failing containment-wiring tests**

`tests/test_segmenter_containment.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.segmenter import ingest_document_text
from smos.memory.vector_store import VectorStore


class _NoStructureAdapter:
    name = "no-structure"
    edge_types: frozenset[str] = frozenset()

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self):
        return None


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_small_document_links_directly_under_document_node(store: VectorStore) -> None:
    text = "A short document with no headings and no need to split further."
    result = ingest_document_text("doc-1", text, store, _NoStructureAdapter())

    assert result["segment_count"] == 1
    assert result["section_count"] == 0

    doc_node = store.graph.get_node("doc-1")
    assert doc_node is not None
    assert doc_node["type"] == "document"

    segment_id = result["segment_ids"][0]
    record = store.get_by_uuid(segment_id)
    assert record["content"] == text
    assert record["metadata"]["doc_id"] == "doc-1"
    assert record["metadata"]["ordinal"] == 0
    assert record["metadata"]["char_start"] == 0
    assert record["metadata"]["char_end"] == len(text)

    neighbors = store.graph.traverse("doc-1", max_hops=1, edge_types=["contains"])
    assert any(n["id"] == segment_id for n in neighbors)


def test_structured_document_creates_section_nodes_for_split_sections(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "Intro paragraph.\n"
        "## Section A\n" + ("Body sentence about topic A. " * 60) + "\n"
        "## Section B\n"
        "Short body B.\n"
    )
    result = ingest_document_text("doc-2", text, store, MarkdownAdapter())

    assert result["section_count"] >= 1
    assert result["segment_count"] == len(result["segment_ids"])

    doc_children = store.graph.traverse("doc-2", max_hops=1, edge_types=["contains"])
    section_nodes = [n for n in doc_children if n["type"] == "section"]
    assert len(section_nodes) == result["section_count"]

    for section in section_nodes:
        section_children = store.graph.traverse(section["id"], max_hops=1, edge_types=["contains"])
        assert len(section_children) >= 2
        for child in section_children:
            assert child["type"] == "memory"


def test_segment_ordinals_are_sequential_and_reconstruct_original_text(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Section A\n" + ("Sentence about A. " * 60) + "\n"
        "## Section B\nShort B.\n"
    )
    result = ingest_document_text("doc-3", text, store, MarkdownAdapter())

    records = sorted(
        (store.get_by_uuid(sid) for sid in result["segment_ids"]),
        key=lambda r: r["metadata"]["ordinal"],
    )
    ordinals = [r["metadata"]["ordinal"] for r in records]
    assert ordinals == list(range(len(records)))

    reconstructed = "".join(
        text[r["metadata"]["char_start"]:r["metadata"]["char_end"]] for r in records
    )
    assert reconstructed == text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_segmenter_containment.py -v`
Expected: FAIL with `ImportError: cannot import name 'ingest_document_text' from 'smos.ingest.segmenter'`.

- [ ] **Step 3: Implement `_wire_containment` and `ingest_document_text`**

Append to `smos/ingest/segmenter.py` (add these imports to the existing import block at the top of the file):

```python
from smos.memory.graph_store import GraphStore
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
```

Then append the two functions:

```python
def _wire_containment(graph: GraphStore, doc_id: str, root: Span) -> int:
    """Wire `contains` edges for the containment tree, capped at exactly
    three levels (document -> section -> segment) per the Phase 5 spec's
    resolved containment-depth decision — regardless of how deeply the
    cascade nested structurally. A section node is only created for a
    top-level child that was itself split further; a top-level child that is
    already a leaf links directly under the document (skipping a redundant
    single-child section). Returns the number of section nodes created."""
    if root.is_leaf:
        graph.add_edge(doc_id, root.memory_id, "contains")
        return 0

    section_count = 0
    for i, child in enumerate(root.children):
        if child.is_leaf:
            graph.add_edge(doc_id, child.memory_id, "contains")
            continue

        section_id = f"{doc_id}:section:{i}"
        graph.add_node(section_id, "section", section_id)
        graph.add_edge(doc_id, section_id, "contains")
        section_count += 1
        for leaf in _flatten_leaves(child):
            graph.add_edge(section_id, leaf.memory_id, "contains")

    return section_count


def ingest_document_text(
    doc_id: str,
    text: str,
    store: VectorStore,
    adapter: DomainAdapter,
    doc_name: Optional[str] = None,
) -> dict:
    """Segment `text` via the structural -> semantic -> size-fit cascade,
    store each leaf as a searchable `segment` memory, and wire the
    containment tree (document -> section -> segment) into the memory graph
    via `contains` edges."""
    root = _build_span_tree(text, 0, len(text), adapter)
    _merge_undersized(text, root)
    leaves = _flatten_leaves(root)

    store.graph.add_node(doc_id, "document", doc_name or doc_id)

    memories = [
        MemoryObject(
            type="segment",
            content=text[leaf.start:leaf.end],
            doc_id=doc_id,
            ordinal=i,
            char_start=leaf.start,
            char_end=leaf.end,
        )
        for i, leaf in enumerate(leaves)
    ]
    segment_ids = store.store_batch(memories)
    for leaf, segment_id in zip(leaves, segment_ids):
        leaf.memory_id = segment_id

    section_count = _wire_containment(store.graph, doc_id, root)

    return {
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
    }
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_segmenter_containment.py -v --tb=short`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full segmenter + graph suites to confirm no regression**

Run: `pytest tests/test_segmenter_cascade.py tests/test_segmenter_containment.py tests/test_graph_store.py tests/test_graph_schemas.py tests/test_vector_store.py -v --tb=short`
Expected: PASS (all tests in all five files)

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/segmenter.py tests/test_segmenter_containment.py
git commit -m "feat: wire containment tree and add ingest_document_text orchestrator"
```

---

## Final verification

- [ ] **Run the full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: PASS, count higher than the pre-plan 207 (2 new test files plus additions to `test_graph_schemas.py`/`test_graph_store.py`). If you see "No tests collected" unexpectedly, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken.

- [ ] **Update `docs/HANDOFF.md`**

Add a "Phase 5.2 — Segmentation cascade: COMPLETE" section (mirroring the 5.1 entry) summarizing: `section` node type + 5.1-schema migration-guard fix, the structural/semantic/size-fit cascade in `smos/ingest/segmenter.py`, the three-level containment tree (`document` → `section` → segment, section nodes only created for groups needing further splitting), and `ingest_document_text()` as the new entry point. Note explicitly that Layer 4 (LLM-proposed boundaries) was deferred as out of scope (optional per spec), and that wiring this into `ingest_tools.py`/the MCP surface is 5.3's job, not done here. Note that 5.3 (ingestion contract + `ingest_tools.py` integration) is next.

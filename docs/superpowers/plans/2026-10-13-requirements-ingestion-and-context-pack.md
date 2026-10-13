# Requirements Ingestion & Context Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make typed item-to-item links from third-party parsers real graph edges (R1), and add a `tool_requirement_context` primitive that returns sectioned, exact-text, budgeted evidence (linked items, peers, normative sources, coverage facts) for a stored requirement or a draft (R2).

**Architecture:** R1 wires the already-validated-but-ignored `IngestionPayload.source_links` (and `doc_type`) into the graph inside `do_ingest_document_schema`, using a new `smos/ingest/link_wiring.py` plus three small `GraphStore` additions. R2 is a pure, LLM-free module `smos/memory/requirement_context.py` that does its own bounded graph traversal and reuses the existing vector+BM25 RRF machinery for peers and normative candidates; `smos/server.py` gets one thin MCP tool.

**Tech Stack:** Python 3, pydantic v2, SQLite (shared connection via `GraphStore`), FAISS + BM25 (existing), FastMCP, pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-requirements-ingestion-and-context-pack-design.md`

## Spec clarifications decided while planning (flag if wrong)

These resolve points the spec left open; each is also noted in the owning task.

1. **Item addressing:** the tool takes `doc_id` **or** `system` + `native_id` (spec said `item_id`). `system` doubles as the default target system for a draft's `declared_links`.
2. **Normative scoping:** an explicit `normative_tags` argument (any-of) scopes the `normative` section. There is no hidden mapping from a requirement's own tags (ASIL etc.) to standards; the caller says which standards apply.
3. **Baseline comparison:** the spec's "graph on vs off" bench dimension becomes a deterministic end-to-end test asserting the linked parent is returned *because of the link* (rank-independent), not a ranking benchmark.
4. **Tag hygiene:** tags are stored comma-joined, so `validate_payload` now rejects a `domain_tags` entry containing `,` (it would silently split into two tags).
5. **Self-links** (an item linking to itself) are quarantined by `validate_payload`.

## Global Constraints

- Use `python` / `pytest` — never `python3` on this machine. If pytest reports "No tests collected", retry with `rtk proxy pytest ...`.
- Test command: `pytest tests/ -v --tb=short`. `bench_*.py` files are not pytest-collected.
- Edge direction is stored **exactly as the parser sends it**: `source` = the item that owns the link. For `depends_on`, `from` = dependent (downstream), `to` = upstream. Upstream of X = X's **outgoing** `depends_on`; downstream of X = X's **incoming** `depends_on`.
- `parent_of` (parent → child), `member_of` (item → epic/group), `cites` (item → normative document, clause in edge `metadata["clause"]`) are the only new edge types. `contains` keeps meaning document structure and is never used between items.
- No LLM call anywhere in R2. Returned text is exact stored text.
- Do not change `smos/compression/context_builder.py`.
- `GraphStore.add_node` stays `INSERT OR IGNORE`; new behaviour goes in new methods only.
- Document node ids are `derive_doc_id(system, native_id)` (sha1 prefix); never invent another id scheme.
- Token estimate is `len(text) // 4` everywhere.
- Every new `.py` file starts with `from __future__ import annotations`.
- Work on a branch from `track-c-citation-namespacing`'s tip (`git branch --show-current` first); use absolute paths rooted at the repo/worktree for every Read/Edit/Write.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies; each has a pinning test in the task named.

1. A link to an item that has not been ingested yet must show up as an *unresolved* coverage fact, never as a linked item with empty text — Task 4.
2. Cyclic links (A depends_on B, B depends_on A) must terminate and not list the seed — Task 4.
3. A budget smaller than the first item must clip that item (flagged `clipped`), not return an empty section — Task 4.
4. An empty store or a draft matching nothing must return empty sections and `has_upstream: false`, never raise — Task 5.
5. A tag containing `,` or an item linking to itself must be quarantined, not silently corrupt tags/edges — Task 3.
6. Re-ingesting an item with a changed link set must replace only its own link edges; incoming links from other items survive — Task 3.

## File Structure

| File | Responsibility |
|---|---|
| `smos/memory/graph_store.py` (modify) | add `upsert_node`, `get_edges`, `remove_owned_edges` |
| `smos/ingest/adapters/requirements.py` (create) | `RequirementsAdapter`: declares `parent_of`/`member_of`/`cites`; no text behaviour |
| `smos/ingest/adapters/__init__.py` (modify) | register `RequirementsAdapter` at import (so the running server validates the new edge types) |
| `smos/ingest/link_wiring.py` (create) | `wire_source_links(graph, payload, doc_id)` |
| `smos/ingest/contract.py` (modify) | `validate_payload`: self-link + comma-in-tag errors |
| `smos/tools/ingest_tools.py` (modify) | persist `doc_type`/system/native_id/metadata, upsert document node, `doctype:` segment tag, call `wire_source_links`, return `link_count` |
| `smos/memory/requirement_context.py` (create) | R2 pure functions |
| `smos/server.py` (modify) | `tool_requirement_context` |
| `tests/test_graph_store_links.py`, `tests/test_requirements_adapter.py`, `tests/test_source_links.py`, `tests/test_requirement_context.py`, `tests/test_requirement_context_e2e.py` (create) | one per task |

---

### Task 1: GraphStore primitives

**Files:**
- Modify: `smos/memory/graph_store.py` (add three methods after `get_node`, before `remove_node`)
- Test: `tests/test_graph_store_links.py` (create)

**Interfaces:**
- Consumes: existing `GraphStore.__init__(db: sqlite3.Connection)`, `add_node`, `add_edge`, `get_node`.
- Produces:
  - `GraphStore.upsert_node(node_id: str, type: str, name: str, metadata: Optional[dict] = None) -> None` — insert or fully overwrite `type`/`name`/`metadata`.
  - `GraphStore.get_edges(node_id: str, direction: str = "both", edge_types: Optional[list[str]] = None) -> list[dict]` — `direction` in `{"out","in","both"}` else `ValueError`; each dict `{"source_id","target_id","edge_type","weight","metadata"}`, ordered by `edge_type, source_id, target_id`.
  - `GraphStore.remove_owned_edges(source_id: str, origin: str) -> int` — deletes edges whose `source_id` matches and whose metadata `origin` equals `origin`; returns count.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_store_links.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from smos.memory.graph_store import GraphStore


def _graph() -> GraphStore:
    return GraphStore(sqlite3.connect(":memory:", check_same_thread=False))


def test_upsert_node_inserts_then_overwrites_name_and_metadata():
    g = _graph()
    g.upsert_node("d1", "document", "old", metadata={"placeholder": True})
    g.upsert_node("d1", "document", "new", metadata={"doc_type": "requirement"})
    node = g.get_node("d1")
    assert node["name"] == "new"
    assert node["metadata"] == {"doc_type": "requirement"}


def test_add_node_still_ignores_an_existing_row():
    g = _graph()
    g.add_node("d1", "document", "first")
    g.add_node("d1", "document", "second")
    assert g.get_node("d1")["name"] == "first"


def test_get_edges_directions_and_edge_type_filter():
    g = _graph()
    for n in ("a", "b", "c"):
        g.add_node(n, "document", n)
    g.add_edge("a", "b", "depends_on")
    g.add_edge("c", "a", "parent_of")

    assert [(e["source_id"], e["target_id"]) for e in g.get_edges("a", "out")] == [("a", "b")]
    assert [(e["source_id"], e["target_id"]) for e in g.get_edges("a", "in")] == [("c", "a")]
    assert len(g.get_edges("a")) == 2
    assert [e["edge_type"] for e in g.get_edges("a", "both", ["parent_of"])] == ["parent_of"]


def test_get_edges_returns_decoded_metadata():
    g = _graph()
    g.add_node("a", "document", "a")
    g.add_node("b", "document", "b")
    g.add_edge("a", "b", "cites", metadata={"clause": "9.4.2"})
    assert g.get_edges("a", "out")[0]["metadata"] == {"clause": "9.4.2"}


def test_get_edges_rejects_unknown_direction():
    with pytest.raises(ValueError):
        _graph().get_edges("a", "sideways")


def test_remove_owned_edges_removes_only_matching_origin_from_that_source():
    g = _graph()
    for n in ("a", "b", "c", "d"):
        g.add_node(n, "document", n)
    g.add_edge("a", "b", "depends_on", metadata={"origin": "source_link"})
    g.add_edge("a", "c", "contains")
    g.add_edge("d", "a", "depends_on", metadata={"origin": "source_link"})

    assert g.remove_owned_edges("a", "source_link") == 1

    remaining = {(e["source_id"], e["target_id"], e["edge_type"]) for e in g.get_edges("a")}
    assert remaining == {("a", "c", "contains"), ("d", "a", "depends_on")}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_graph_store_links.py -v --tb=short`
Expected: FAIL with `AttributeError: 'GraphStore' object has no attribute 'upsert_node'` (and the others).

- [ ] **Step 3: Implement**

In `smos/memory/graph_store.py`, insert after `get_node` (before `def remove_node`):

```python
    def upsert_node(
        self, node_id: str, type: str, name: str, metadata: Optional[dict] = None
    ) -> None:
        """Insert the node, or overwrite an existing row's type/name/metadata.
        `add_node` stays INSERT OR IGNORE for existing callers; this is the
        explicit refresh path (document re-sync, placeholder upgrade)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO graph_nodes (id, type, name, metadata) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "type = excluded.type, name = excluded.name, metadata = excluded.metadata",
                (node_id, type, name, json.dumps(metadata or {})),
            )
            self._db.commit()

    def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Directed edge listing for one node. `direction` is relative to
        `node_id`: "out" = node is the source, "in" = node is the target."""
        if direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be 'out', 'in' or 'both', got {direction!r}")
        clauses: list[str] = []
        params: list = []
        if direction == "out":
            clauses.append("source_id = ?")
            params.append(node_id)
        elif direction == "in":
            clauses.append("target_id = ?")
            params.append(node_id)
        else:
            clauses.append("(source_id = ? OR target_id = ?)")
            params.extend([node_id, node_id])
        if edge_types:
            placeholders = ",".join("?" for _ in edge_types)
            clauses.append(f"edge_type IN ({placeholders})")
            params.extend(edge_types)
        sql = (
            "SELECT source_id, target_id, edge_type, weight, metadata FROM graph_edges "
            "WHERE " + " AND ".join(clauses) + " ORDER BY edge_type, source_id, target_id"
        )
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [
            {
                "source_id": r[0],
                "target_id": r[1],
                "edge_type": r[2],
                "weight": r[3],
                "metadata": json.loads(r[4]) if r[4] else {},
            }
            for r in rows
        ]

    def remove_owned_edges(self, source_id: str, origin: str) -> int:
        """Delete edges leaving `source_id` whose metadata `origin` equals
        `origin` (e.g. the `source_link` edges one document's payload owns).
        Edges of other origins (`contains`, ...) and incoming edges are kept."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, metadata FROM graph_edges WHERE source_id = ?", (source_id,)
            ).fetchall()
            doomed = [
                row[0]
                for row in rows
                if (json.loads(row[1]) if row[1] else {}).get("origin") == origin
            ]
            for edge_id in doomed:
                self._db.execute("DELETE FROM graph_edges WHERE id = ?", (edge_id,))
            self._db.commit()
        return len(doomed)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_graph_store_links.py -v --tb=short`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add smos/memory/graph_store.py tests/test_graph_store_links.py
git commit -m "feat: GraphStore upsert_node, get_edges, remove_owned_edges"
```

---

### Task 2: RequirementsAdapter and edge vocabulary

**Files:**
- Create: `smos/ingest/adapters/requirements.py`
- Modify: `smos/ingest/adapters/__init__.py`
- Test: `tests/test_requirements_adapter.py` (create)

**Interfaces:**
- Consumes: `DomainAdapter` protocol (`smos/ingest/adapters/base.py`), `register_adapter`, `get_adapter`, `all_edge_types`, `reset_registry` from `smos/ingest/adapters/registry.py`; `validate_payload` from `smos/ingest/contract.py`.
- Produces: `RequirementsAdapter` with `name = "requirements"`, `edge_types = frozenset({"parent_of","member_of","cites"})`; registered at `import smos.ingest.adapters`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_requirements_adapter.py`:

```python
from __future__ import annotations

import importlib

import pytest

from smos.ingest.adapters.registry import (
    all_edge_types,
    get_adapter,
    register_adapter,
    reset_registry,
)
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, SourceLink, validate_payload


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _payload(edge_type: str) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="cb", native_id="1"),
        blocks=[ContentBlock(block_type="paragraph", text="The system shall stop.")],
        source_links=[SourceLink(target_native_id="2", edge_type=edge_type)],
    )


def test_adapter_declares_traceability_edge_types():
    adapter = RequirementsAdapter()
    assert adapter.name == "requirements"
    assert adapter.edge_types == frozenset({"parent_of", "member_of", "cites"})


def test_adapter_has_no_text_behaviour():
    adapter = RequirementsAdapter()
    assert adapter.structural_boundaries("Some text.") == []
    assert adapter.extract_references("See [REQ:12].") == []
    assert adapter.llm_boundary_prompt() is None


def test_new_edge_types_are_rejected_until_the_adapter_is_registered():
    errors = validate_payload(_payload("parent_of"))
    assert any("unknown edge_type 'parent_of'" in e for e in errors)

    register_adapter(RequirementsAdapter())
    assert validate_payload(_payload("parent_of")) == []
    assert validate_payload(_payload("member_of")) == []
    assert validate_payload(_payload("cites")) == []


def test_core_depends_on_needs_no_registration():
    assert validate_payload(_payload("depends_on")) == []


def test_package_import_registers_requirements_adapter():
    import smos.ingest.adapters as adapters_pkg

    importlib.reload(adapters_pkg)
    assert get_adapter("requirements") is not None
    assert {"parent_of", "member_of", "cites"} <= all_edge_types()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_requirements_adapter.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.adapters.requirements'`.

- [ ] **Step 3: Implement**

Create `smos/ingest/adapters/requirements.py`:

```python
from __future__ import annotations

from typing import Optional


class RequirementsAdapter:
    """Declares the traceability edge vocabulary for requirements-style
    corpora. Deliberately behaviourless on text: the third-party parser that
    produces the ingestion payload already knows the links and sends them as
    `source_links`, so there is nothing to extract from prose.

    Edge types (all item -> item, direction exactly as the parser sends it):
      parent_of   parent item -> child item (tree hierarchy)
      member_of   item -> epic / group it belongs to
      cites       item -> normative document (legal / standard); the cited
                  clause travels in the link's metadata (`clause`)
    `depends_on` and `references` are already core vocabulary.
    """

    name = "requirements"
    edge_types: frozenset[str] = frozenset({"parent_of", "member_of", "cites"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
```

Replace the contents of `smos/ingest/adapters/__init__.py` with:

```python
from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.adapters.registry import register_adapter
from smos.ingest.adapters.requirements import RequirementsAdapter

register_adapter(MarkdownAdapter())
register_adapter(RequirementsAdapter())
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_requirements_adapter.py tests/test_adapter_registry.py tests/test_markdown_adapter.py tests/test_contract_validation.py -v --tb=short`
Expected: all pass (the other three files reset the registry in their own fixtures, so auto-registration does not affect them).

- [ ] **Step 5: Commit**

```bash
git add smos/ingest/adapters/requirements.py smos/ingest/adapters/__init__.py tests/test_requirements_adapter.py
git commit -m "feat: RequirementsAdapter declaring parent_of/member_of/cites edge vocabulary"
```

---

### Task 3: Wire `source_links` and `doc_type` into the graph (R1)

**Files:**
- Create: `smos/ingest/link_wiring.py`
- Modify: `smos/ingest/contract.py` (`validate_payload`)
- Modify: `smos/tools/ingest_tools.py` (import line ~28-38; document node block ~682-685; segment tags ~705; wiring + return dict ~722-741)
- Test: `tests/test_source_links.py` (create)

**Interfaces:**
- Consumes: Task 1 `GraphStore.upsert_node/get_edges/remove_owned_edges/add_node/add_edge/get_node`; Task 2 vocabulary (tests register it); `derive_doc_id(system, native_id) -> str`.
- Produces:
  - `smos.ingest.link_wiring.LINK_ORIGIN = "source_link"`.
  - `wire_source_links(graph: GraphStore, payload: IngestionPayload, doc_id: str) -> int` (number of links processed).
  - `do_ingest_document_schema` result gains `"link_count": int`.
  - Document node metadata keys: `domain_tags`, `doc_type`, `system`, `native_id`, `metadata`. Placeholder node metadata: `{"placeholder": True, "system", "native_id"}`, node name = target native id.
  - Segments gain tag `doctype:<doc_type>` when `doc_type` is set.
  - Link edge metadata: link's own `metadata` (minus `target_system`) plus `{"origin": "source_link"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_links.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    derive_doc_id,
)
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_adapter(RequirementsAdapter())
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _item(native_id, text, *, system="cb-test", name=None, links=(), tags=(),
          doc_type="requirement", metadata=None) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=name or native_id,
        blocks=[ContentBlock(block_type="paragraph", text=text)],
        doc_type=doc_type,
        domain_tags=list(tags),
        metadata=metadata or {},
        source_links=[
            SourceLink(target_native_id=t, edge_type=e, metadata=m) for (t, e, m) in links
        ],
    )


def _id(native_id: str, system: str = "cb-test") -> str:
    return derive_doc_id(system, native_id)


def _out(store: VectorStore, native_id: str, system: str = "cb-test") -> set[tuple[str, str]]:
    return {
        (e["target_id"], e["edge_type"])
        for e in store.graph.get_edges(_id(native_id, system), "out")
        if e["edge_type"] != "contains"
    }


def test_depends_on_link_creates_edge_in_the_direction_sent(store):
    result = do_ingest_document_schema(
        _item("SYS2-1", "The unit shall brake.", links=[("SYS1-1", "depends_on", {})]), store
    )
    assert result["status"] == "success"
    assert result["link_count"] == 1
    assert _out(store, "SYS2-1") == {(_id("SYS1-1"), "depends_on")}


def test_missing_target_becomes_placeholder_then_is_upgraded_by_real_ingest(store):
    do_ingest_document_schema(
        _item("SYS2-1", "The unit shall brake.", links=[("SYS1-1", "depends_on", {})]), store
    )
    placeholder = store.graph.get_node(_id("SYS1-1"))
    assert placeholder["type"] == "document"
    assert placeholder["metadata"]["placeholder"] is True
    assert placeholder["name"] == "SYS1-1"

    do_ingest_document_schema(
        _item("SYS1-1", "Occupants shall be protected.", name="Occupant protection"), store
    )
    upgraded = store.graph.get_node(_id("SYS1-1"))
    assert upgraded["name"] == "Occupant protection"
    assert "placeholder" not in upgraded["metadata"]
    assert upgraded["metadata"]["doc_type"] == "requirement"
    assert _out(store, "SYS2-1") == {(_id("SYS1-1"), "depends_on")}


def test_target_system_in_link_metadata_selects_the_target_namespace(store):
    do_ingest_document_schema(
        _item("R-1", "The unit shall log.", links=[("ISO-9", "cites", {"target_system": "std", "clause": "9.4.2"})]),
        store,
    )
    edges = store.graph.get_edges(_id("R-1"), "out", ["cites"])
    assert [e["target_id"] for e in edges] == [derive_doc_id("std", "ISO-9")]
    assert edges[0]["metadata"] == {"clause": "9.4.2", "origin": "source_link"}


def test_resync_replaces_owned_edges_and_keeps_incoming_edges(store):
    do_ingest_document_schema(_item("A", "Alpha text one.", links=[("B", "depends_on", {})]), store)
    do_ingest_document_schema(_item("D", "Delta text.", links=[("A", "depends_on", {})]), store)
    do_ingest_document_schema(_item("A", "Alpha text two.", links=[("C", "depends_on", {})]), store)

    assert _out(store, "A") == {(_id("C"), "depends_on")}
    assert (_id("A"), "depends_on") in _out(store, "D")


def test_doc_type_and_identity_persist_on_node_and_segments(store):
    result = do_ingest_document_schema(
        _item("R-2", "The unit shall report faults.", tags=["domain:safety"], metadata={"status": "Draft"}),
        store,
    )
    node = store.graph.get_node(_id("R-2"))
    assert node["metadata"]["doc_type"] == "requirement"
    assert node["metadata"]["system"] == "cb-test"
    assert node["metadata"]["native_id"] == "R-2"
    assert node["metadata"]["metadata"] == {"status": "Draft"}
    assert node["metadata"]["domain_tags"] == ["domain:safety"]

    tags = store.get_by_uuid(result["segment_ids"][0])["metadata"]["tags"].split(",")
    assert "doctype:requirement" in tags
    assert "domain:safety" in tags


def test_resync_refreshes_document_name(store):
    do_ingest_document_schema(_item("R-3", "Text one.", name="Old name"), store)
    do_ingest_document_schema(_item("R-3", "Text two.", name="New name"), store)
    assert store.graph.get_node(_id("R-3"))["name"] == "New name"


def test_self_link_is_quarantined(store):
    result = do_ingest_document_schema(
        _item("R-4", "The unit shall idle.", links=[("R-4", "depends_on", {})]), store
    )
    assert result["status"] == "quarantined"
    assert any("targets its own item" in e for e in result["errors"])


def test_comma_in_domain_tag_is_quarantined(store):
    result = do_ingest_document_schema(_item("R-5", "The unit shall idle.", tags=["a,b"]), store)
    assert result["status"] == "quarantined"
    assert any("must not contain ','" in e for e in result["errors"])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_source_links.py -v --tb=short`
Expected: FAIL — `KeyError: 'link_count'` / missing edges / no quarantine.

- [ ] **Step 3: Implement**

Create `smos/ingest/link_wiring.py`:

```python
from __future__ import annotations

from smos.ingest.contract import IngestionPayload, derive_doc_id
from smos.memory.graph_store import GraphStore

LINK_ORIGIN = "source_link"


def wire_source_links(graph: GraphStore, payload: IngestionPayload, doc_id: str) -> int:
    """Turn `payload.source_links` into `document --edge_type--> document`
    edges, direction exactly as sent.

    Edges this document owns (origin == "source_link") are replaced wholesale,
    so a re-sync with a changed link set drops stale links but leaves every
    other document's links to this one intact. A target that has not been
    ingested yet gets a placeholder `document` node (metadata.placeholder =
    True) that the real ingest later overwrites via `upsert_node`.

    `link.metadata["target_system"]` selects the target's namespace; without
    it the target lives in the payload's own system. All other link metadata
    (e.g. a cited `clause`) is stored on the edge."""
    graph.remove_owned_edges(doc_id, LINK_ORIGIN)
    count = 0
    for link in payload.source_links:
        edge_metadata = dict(link.metadata)
        target_system = edge_metadata.pop("target_system", None) or payload.external_handle.system
        target_id = derive_doc_id(target_system, link.target_native_id)
        if graph.get_node(target_id) is None:
            graph.add_node(
                target_id,
                "document",
                link.target_native_id,
                metadata={
                    "placeholder": True,
                    "system": target_system,
                    "native_id": link.target_native_id,
                },
            )
        graph.add_edge(
            doc_id, target_id, link.edge_type,
            metadata={**edge_metadata, "origin": LINK_ORIGIN},
        )
        count += 1
    return count
```

In `smos/ingest/contract.py`, replace the existing edge-type loop in `validate_payload`:

```python
    valid_edge_types = all_edge_types()
    for i, link in enumerate(payload.source_links):
        if link.edge_type not in valid_edge_types:
            errors.append(f"source_links[{i}]: unknown edge_type '{link.edge_type}'")
```

with:

```python
    valid_edge_types = all_edge_types()
    own_system = payload.external_handle.system
    own_native_id = payload.external_handle.native_id
    for i, link in enumerate(payload.source_links):
        if link.edge_type not in valid_edge_types:
            errors.append(f"source_links[{i}]: unknown edge_type '{link.edge_type}'")
        target_system = link.metadata.get("target_system") or own_system
        if target_system == own_system and link.target_native_id == own_native_id:
            errors.append(f"source_links[{i}]: link targets its own item")

    for i, tag in enumerate(payload.domain_tags):
        if "," in tag:
            errors.append(f"domain_tags[{i}]: must not contain ','")
```

In `smos/tools/ingest_tools.py`:

1. Add `from smos.ingest.link_wiring import wire_source_links` directly after the `from smos.ingest.segmenter import ...` import line.
2. Replace

```python
    store.graph.add_node(
        doc_id, "document", payload.doc_name or doc_id,
        metadata={"domain_tags": payload.domain_tags},
    )
```

with

```python
    store.graph.upsert_node(
        doc_id, "document", payload.doc_name or doc_id,
        metadata={
            "domain_tags": payload.domain_tags,
            "doc_type": payload.doc_type,
            "system": payload.external_handle.system,
            "native_id": payload.external_handle.native_id,
            "metadata": payload.metadata,
        },
    )
    segment_tags = list(payload.domain_tags)
    if payload.doc_type:
        segment_tags.append(f"doctype:{payload.doc_type}")
```

3. In the `MemoryObject(...)` construction inside the chunk loop, change `tags=list(payload.domain_tags),` to `tags=list(segment_tags),`.
4. After the `reference_count = _wire_references(...)` line add `link_count = wire_source_links(store.graph, payload, doc_id)`, and add `"link_count": link_count,` to the returned success dict (after `"reference_count"`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_source_links.py tests/test_contract_ingest.py tests/test_contract_validation.py tests/test_reference_and_domain_tagging_e2e.py -v --tb=short`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q --tb=short -p no:warnings`
Expected: all pass (about 6-7 minutes; run in the background if the harness times out).

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/link_wiring.py smos/ingest/contract.py smos/tools/ingest_tools.py tests/test_source_links.py
git commit -m "feat: wire source_links and doc_type into the graph on ingest"
```

---

### Task 4: `requirement_context` — resolution, `linked`, coverage, budget

**Files:**
- Create: `smos/memory/requirement_context.py`
- Test: `tests/test_requirement_context.py` (create)

**Interfaces:**
- Consumes: `VectorStore.graph` (`get_node`, `get_edges`), `derive_doc_id`, `smos.memory.exact_retrieval.reconstruct(store, node_id) -> dict` (keys `text`, `complete`, `error`), Task 3 stored data.
- Produces (`smos/memory/requirement_context.py`):
  - constants `LINK_EDGE_TYPES = ["depends_on", "parent_of", "member_of"]`, `NORMATIVE_DOCTYPES = ("legal", "standard")`.
  - `requirement_context(store, *, text="", doc_id="", system="", native_id="", level="", domain_tags=None, normative_tags=None, declared_links=None, token_budget=4000, k_per_section=5) -> dict`.
  - Return shape: `{"query": {"doc_id": str|None, "text": str, "level": str, "domain_tags": list[str]}, "linked": [item], "peers": [item], "normative": [item], "coverage": {...}, "truncated": {"linked": bool, "peers": bool, "normative": bool}}`; on bad input `{"error": str}`.
  - item: `{"id","doc_id","doc_name","text","score","reason","hops","path", "clipped"}` plus, for peers, `"segment_id"` and `"near_duplicate"`, for normative, `"segment_id"` and `"clause"`.
  - `coverage`: `{"has_upstream": bool, "has_downstream": bool, "near_duplicates": int, "normative_by_source": {"legal": int, "standard": int}, "unresolved_links": list[str]}`.
  - helpers used by Task 5: `_fit(items, budget_tokens) -> tuple[list[dict], bool]`, `_doc_text(store, doc_id) -> str`, `_placeholder(node) -> bool`.
- In this task `peers` and `normative` are returned as empty lists with `truncated` False and `near_duplicates`/`normative_by_source` zero; Task 5 replaces those two lines.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_requirement_context.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, SourceLink
from smos.memory.requirement_context import requirement_context
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

SYSTEM = "cb-test"


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_adapter(RequirementsAdapter())
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _ingest(store, native_id, text, *, links=(), tags=(), doc_type="requirement",
            system=SYSTEM, name=None):
    payload = IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=name or native_id,
        blocks=[ContentBlock(block_type="paragraph", text=text)],
        doc_type=doc_type,
        domain_tags=list(tags),
        source_links=[SourceLink(target_native_id=t, edge_type=e, metadata=m) for (t, e, m) in links],
    )
    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "success", result
    return result


S1_TEXT = "Occupants must be protected from harm during an abrupt stop."
S2_TEXT = "The braking controller shall apply full deceleration within 150 ms of a collision signal."
S3_TEXT = "The brake actuator driver shall assert the hydraulic valve line on a collision flag."


def _chain(store):
    _ingest(store, "S1", S1_TEXT)
    _ingest(store, "S2", S2_TEXT, links=[("S1", "depends_on", {})])
    _ingest(store, "S3", S3_TEXT, links=[("S2", "depends_on", {})])


def _by_id(items):
    return {i["id"]: i for i in items}


def test_linked_returns_upstream_and_downstream_with_exact_text(store):
    _chain(store)
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="S2")
    linked = _by_id(result["linked"])

    s1, s3 = derive_doc_id(SYSTEM, "S1"), derive_doc_id(SYSTEM, "S3")
    assert set(linked) == {s1, s3}
    assert linked[s1]["reason"] == "upstream (depends_on)"
    assert linked[s1]["text"] == S1_TEXT
    assert linked[s1]["hops"] == 1
    assert linked[s3]["reason"] == "downstream (depends_on)"
    assert linked[s3]["text"] == S3_TEXT
    assert result["coverage"]["has_upstream"] is True
    assert result["coverage"]["has_downstream"] is True


def test_two_hop_neighbour_is_included_with_hop_count(store):
    _chain(store)
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="S3")
    linked = _by_id(result["linked"])
    s1 = derive_doc_id(SYSTEM, "S1")
    assert linked[s1]["hops"] == 2
    assert [p["edge_type"] for p in linked[s1]["path"]] == ["depends_on", "depends_on"]


def test_parent_of_is_labelled_from_the_childs_point_of_view(store):
    _ingest(store, "P", "The vehicle shall provide a braking function.", links=[("C", "parent_of", {})])
    _ingest(store, "C", "The braking controller shall compute pedal demand.")
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="C")
    item = _by_id(result["linked"])[derive_doc_id(SYSTEM, "P")]
    assert item["reason"] == "parent (parent_of)"


def test_unresolved_link_is_reported_not_listed(store):
    _ingest(store, "A", "The unit shall start up.", links=[("MISSING", "depends_on", {})])
    result = requirement_context(store, system=SYSTEM, native_id="A")
    assert result["linked"] == []
    assert result["coverage"]["unresolved_links"] == ["MISSING"]
    assert result["coverage"]["has_upstream"] is True


def test_cyclic_links_terminate_and_exclude_the_seed(store):
    _ingest(store, "A", "Alpha shall start.", links=[("B", "depends_on", {})])
    _ingest(store, "B", "Bravo shall stop.", links=[("A", "depends_on", {})])
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="A")
    assert [i["id"] for i in result["linked"]] == [derive_doc_id(SYSTEM, "B")]


def test_draft_with_declared_link_resolves_the_target(store):
    _chain(store)
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(
        store,
        text="The controller shall brake hard on impact.",
        system=SYSTEM,
        declared_links=[{"target_native_id": "S1", "edge_type": "depends_on"}],
    )
    linked = _by_id(result["linked"])
    s1 = derive_doc_id(SYSTEM, "S1")
    assert linked[s1]["reason"] == "declared depends_on"
    assert result["coverage"]["has_upstream"] is True
    assert result["query"]["doc_id"] is None


def test_unknown_declared_target_is_reported_unresolved(store):
    result = requirement_context(
        store,
        text="The controller shall brake hard on impact.",
        system=SYSTEM,
        declared_links=[{"target_native_id": "NOPE", "edge_type": "depends_on"}],
    )
    assert result["linked"] == []
    assert result["coverage"]["unresolved_links"] == ["NOPE"]


def test_token_budget_smaller_than_first_item_clips_it(store):
    _chain(store)
    result = requirement_context(store, system=SYSTEM, native_id="S2", token_budget=6)
    # per-section budget = 6 // 3 = 2 tokens = 8 characters
    assert result["linked"], "a section must not come back empty just because the budget is tiny"
    first = result["linked"][0]
    assert first["clipped"] is True
    assert len(first["text"]) <= 8
    assert result["truncated"]["linked"] is True


def test_no_input_is_an_error(store):
    assert "error" in requirement_context(store)


def test_unknown_stored_item_is_an_error(store):
    assert "error" in requirement_context(store, system=SYSTEM, native_id="NOPE")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_requirement_context.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.memory.requirement_context'`.

- [ ] **Step 3: Implement**

Create `smos/memory/requirement_context.py`:

```python
from __future__ import annotations

from collections import deque
from typing import Optional

from smos.ingest.contract import derive_doc_id
from smos.memory.exact_retrieval import reconstruct
from smos.memory.vector_store import VectorStore

LINK_EDGE_TYPES = ["depends_on", "parent_of", "member_of"]
NORMATIVE_DOCTYPES = ("legal", "standard")
_MAX_HOPS = 2
_DEFAULT_TOKEN_BUDGET = 4000
_DEFAULT_K = 5
_SECTIONS = ("linked", "peers", "normative")

# (edge_type, direction relative to the node we walked FROM) -> label
_RELATION_LABELS = {
    ("depends_on", "out"): "upstream (depends_on)",
    ("depends_on", "in"): "downstream (depends_on)",
    ("parent_of", "out"): "child (parent_of)",
    ("parent_of", "in"): "parent (parent_of)",
    ("member_of", "out"): "member of (member_of)",
    ("member_of", "in"): "has member (member_of)",
}
_EDGE_ORDER = {t: i for i, t in enumerate(LINK_EDGE_TYPES)}


def _placeholder(node: Optional[dict]) -> bool:
    return bool(node and node.get("metadata", {}).get("placeholder"))


def _doc_text(store: VectorStore, doc_id: str) -> str:
    return reconstruct(store, doc_id).get("text", "")


def _cost(text: str) -> int:
    return len(text) // 4 + 1


def _fit(items: list[dict], budget_tokens: int) -> tuple[list[dict], bool]:
    """Keep items in order while they fit `budget_tokens`. If even the first
    item does not fit it is clipped (flagged `clipped`) rather than dropped,
    so a section is never empty merely because the budget is small."""
    kept: list[dict] = []
    used = 0
    for item in items:
        cost = _cost(item["text"])
        if used + cost > budget_tokens:
            if not kept:
                clipped = dict(item)
                clipped["text"] = item["text"][: max(budget_tokens, 1) * 4]
                clipped["clipped"] = True
                kept.append(clipped)
            return kept, True
        entry = dict(item)
        entry.setdefault("clipped", False)
        kept.append(entry)
        used += cost
    return kept, False


def _walk_links(store: VectorStore, starts: list[tuple[str, int, list[dict], str]], seed: Optional[str]):
    """BFS over LINK_EDGE_TYPES (both directions) up to _MAX_HOPS.
    `starts` = (node_id, hops, path, reason) entries to enqueue. Returns
    {node_id: (hops, path, reason)} excluding `seed`."""
    found: dict[str, tuple[int, list[dict], str]] = {}
    visited = {seed} if seed else set()
    queue: deque = deque()
    for node_id, hops, path, reason in starts:
        if node_id in visited:
            continue
        visited.add(node_id)
        found[node_id] = (hops, path, reason)
        queue.append(node_id)
    if seed:
        queue.appendleft(seed)

    while queue:
        node_id = queue.popleft()
        hops = 0 if node_id == seed else found[node_id][0]
        if hops >= _MAX_HOPS:
            continue
        base_path = [] if node_id == seed else found[node_id][1]
        for edge in store.graph.get_edges(node_id, "both", LINK_EDGE_TYPES):
            outgoing = edge["source_id"] == node_id
            other = edge["target_id"] if outgoing else edge["source_id"]
            if other in visited:
                continue
            visited.add(other)
            direction = "out" if outgoing else "in"
            step = {"edge_type": edge["edge_type"], "direction": direction, "node": other}
            found[other] = (
                hops + 1,
                base_path + [step],
                _RELATION_LABELS.get((edge["edge_type"], direction), edge["edge_type"]),
            )
            queue.append(other)
    return found


def requirement_context(
    store: VectorStore,
    *,
    text: str = "",
    doc_id: str = "",
    system: str = "",
    native_id: str = "",
    level: str = "",
    domain_tags: Optional[list[str]] = None,
    normative_tags: Optional[list[str]] = None,
    declared_links: Optional[list[dict]] = None,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    k_per_section: int = _DEFAULT_K,
) -> dict:
    """Sectioned, exact-text evidence for judging a requirement. Input is a
    stored item (`doc_id`, or `system` + `native_id`) and/or a draft `text`.
    No LLM is called; SMOS returns evidence and plain coverage facts only."""
    graph = store.graph
    stored_id = doc_id or (derive_doc_id(system, native_id) if system and native_id else "")
    tags = list(domain_tags or [])

    if stored_id:
        node = graph.get_node(stored_id)
        if node is None or node["type"] != "document" or _placeholder(node):
            return {"error": f"no ingested item for doc_id {stored_id}"}
        if not text:
            text = _doc_text(store, stored_id)
        if not tags:
            tags = list(node["metadata"].get("domain_tags") or [])
        if not system:
            system = node["metadata"].get("system", "") or ""
    elif not text.strip():
        return {"error": "provide text, doc_id, or system + native_id"}

    if not level:
        level = next((t.split(":", 1)[1] for t in tags if t.startswith("level:")), "")

    unresolved: list[str] = []
    starts: list[tuple[str, int, list[dict], str]] = []
    declared_cites: list[dict] = []
    has_upstream = False
    for link in declared_links or []:
        target_system = link.get("target_system") or system
        target_native = link.get("target_native_id", "")
        edge_type = link.get("edge_type", "")
        if not target_system or not target_native:
            unresolved.append(target_native)
            continue
        target_id = derive_doc_id(target_system, target_native)
        target_node = graph.get_node(target_id)
        if target_node is None or _placeholder(target_node):
            unresolved.append(target_native)
            continue
        if edge_type == "depends_on":
            has_upstream = True
        if edge_type == "cites":
            declared_cites.append({"target_id": target_id, "clause": link.get("clause")})
        elif edge_type in LINK_EDGE_TYPES:
            starts.append((
                target_id, 1,
                [{"edge_type": edge_type, "direction": "declared", "node": target_id}],
                f"declared {edge_type}",
            ))

    has_downstream = False
    stored_cites: list[dict] = []
    if stored_id:
        for edge in graph.get_edges(stored_id, "out", ["depends_on"]):
            has_upstream = True
        has_downstream = bool(graph.get_edges(stored_id, "in", ["depends_on"]))
        for edge in graph.get_edges(stored_id, "out", ["cites"]):
            stored_cites.append({
                "target_id": edge["target_id"],
                "clause": edge["metadata"].get("clause"),
            })

    found = _walk_links(store, starts, stored_id or None)

    linked_items: list[dict] = []
    for node_id, (hops, path, reason) in found.items():
        node = graph.get_node(node_id)
        if node is None or node["type"] != "document":
            continue
        if _placeholder(node):
            native = node["metadata"].get("native_id") or node["name"]
            if native not in unresolved:
                unresolved.append(native)
            continue
        linked_items.append({
            "id": node_id,
            "doc_id": node_id,
            "doc_name": node["name"],
            "text": _doc_text(store, node_id),
            "score": None,
            "reason": reason,
            "hops": hops,
            "path": path,
        })
    linked_items.sort(key=lambda i: (
        i["hops"], _EDGE_ORDER.get(i["path"][-1]["edge_type"], 99), i["doc_name"],
    ))

    # an unresolved outgoing link on the stored item itself also counts
    if stored_id:
        for edge in graph.get_edges(stored_id, "out", LINK_EDGE_TYPES + ["cites"]):
            target = graph.get_node(edge["target_id"])
            if _placeholder(target):
                native = target["metadata"].get("native_id") or target["name"]
                if native not in unresolved:
                    unresolved.append(native)

    section_budget = max(token_budget // len(_SECTIONS), 1)
    k = max(k_per_section, 1)
    linked, linked_truncated = _fit(linked_items[:k], section_budget)

    peers: list[dict] = []
    normative: list[dict] = []
    return {
        "query": {
            "doc_id": stored_id or None,
            "text": text,
            "level": level,
            "domain_tags": tags,
        },
        "linked": linked,
        "peers": peers,
        "normative": normative,
        "coverage": {
            "has_upstream": has_upstream,
            "has_downstream": has_downstream,
            "near_duplicates": 0,
            "normative_by_source": {d: 0 for d in NORMATIVE_DOCTYPES},
            "unresolved_links": unresolved,
        },
        "truncated": {"linked": linked_truncated, "peers": False, "normative": False},
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_requirement_context.py -v --tb=short`
Expected: 10 passed.

Note for the implementer: `declared_cites` and `stored_cites` are built here and consumed by Task 5; they are intentionally unused in this task's return value.

- [ ] **Step 5: Commit**

```bash
git add smos/memory/requirement_context.py tests/test_requirement_context.py
git commit -m "feat: requirement_context linked section, coverage facts and token budget"
```

---

### Task 5: `peers` and `normative` sections

**Files:**
- Modify: `smos/memory/requirement_context.py`
- Modify: `tests/test_requirement_context.py` (append tests)

**Interfaces:**
- Consumes: Task 4's `requirement_context`, `_fit`, `_doc_text`, `_placeholder`, `NORMATIVE_DOCTYPES`; `smos.compression.context_builder._rrf_merge(vector_results, bm25_results, top_k) -> list[tuple[str, float]]`; `VectorStore.query(text, k) -> list[dict]` (each with `id`, `content`, `metadata{type,tags,doc_id,...}`, `score`), `VectorStore.bm25.query(text, k) -> list[tuple[str, float]]`, `VectorStore.get_by_uuid`, `VectorStore.get_segment_by_ordinal(doc_id, ordinal)`.
- Produces: filled `peers` / `normative` sections, `coverage.near_duplicates`, `coverage.normative_by_source`; module constant `_NEAR_DUPLICATE_THRESHOLD = 0.90`.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_requirement_context.py`:

```python
DUP_A = "The brake controller shall limit pedal force to 400 newtons under normal operation."
DUP_B = "The brake controller shall limit pedal force to 450 newtons under normal operation."
OTHER = "The infotainment display shall show the current song title within one second."
STANDARD_TEXT = (
    "9.4.2 The braking system shall be designed so that a single fault does not "
    "lead to loss of deceleration capability."
)
LEGAL_TEXT = (
    "Article 7. Manufacturers shall ensure that braking systems of motor vehicles "
    "meet the prescribed stopping distances."
)


def test_peers_flag_near_duplicates_and_respect_level_and_self_exclusion(store):
    _ingest(store, "R1", DUP_A, tags=["level:SYS.2"])
    _ingest(store, "R2", DUP_B, tags=["level:SYS.2"])
    _ingest(store, "R3", OTHER, tags=["level:SYS.3"])
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="R1", level="SYS.2")
    peers = _by_id(result["peers"])

    assert derive_doc_id(SYSTEM, "R1") not in peers
    assert derive_doc_id(SYSTEM, "R3") not in peers
    r2 = peers[derive_doc_id(SYSTEM, "R2")]
    assert r2["near_duplicate"] is True
    assert r2["text"] == DUP_B
    assert result["coverage"]["near_duplicates"] == 1


def test_peers_exclude_items_already_in_linked(store):
    _ingest(store, "U", "The brake controller shall limit pedal force under normal operation.")
    _ingest(store, "D", DUP_A, links=[("U", "depends_on", {})])
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="D")
    assert derive_doc_id(SYSTEM, "U") in _by_id(result["linked"])
    assert derive_doc_id(SYSTEM, "U") not in _by_id(result["peers"])


def test_peers_exclude_normative_documents(store):
    _ingest(store, "R1", DUP_A)
    _ingest(store, "STD-1", STANDARD_TEXT, doc_type="standard", tags=["iso26262"])
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="R1")
    assert derive_doc_id(SYSTEM, "STD-1") not in _by_id(result["peers"])


def test_normative_returns_legal_and_standard_with_counts(store):
    _ingest(store, "STD-1", STANDARD_TEXT, doc_type="standard", tags=["iso26262"])
    _ingest(store, "LAW-1", LEGAL_TEXT, doc_type="legal", tags=["eu-law"])
    result = requirement_context(
        store, text="The braking system shall keep decelerating after a single fault."
    )
    sources = {i["doc_id"] for i in result["normative"]}
    assert len(sources) == 2
    assert result["coverage"]["normative_by_source"] == {"legal": 1, "standard": 1}


def test_normative_tags_scope_the_section(store):
    _ingest(store, "STD-1", STANDARD_TEXT, doc_type="standard", tags=["iso26262"])
    _ingest(store, "LAW-1", LEGAL_TEXT, doc_type="legal", tags=["eu-law"])
    result = requirement_context(
        store,
        text="The braking system shall keep decelerating after a single fault.",
        normative_tags=["iso26262"],
    )
    assert result["coverage"]["normative_by_source"] == {"legal": 0, "standard": 1}


def test_explicit_cites_come_first_with_clause(store):
    _ingest(store, "STD-1", STANDARD_TEXT, doc_type="standard", tags=["iso26262"])
    _ingest(store, "LAW-1", LEGAL_TEXT, doc_type="legal", tags=["eu-law"])
    _ingest(
        store, "R9", "The unit shall log every actuator fault.",
        links=[("LAW-1", "cites", {"clause": "Article 7"})],
    )
    from smos.ingest.contract import derive_doc_id

    result = requirement_context(store, system=SYSTEM, native_id="R9")
    first = result["normative"][0]
    assert first["doc_id"] == derive_doc_id(SYSTEM, "LAW-1")
    assert first["reason"] == "explicit citation"
    assert first["clause"] == "Article 7"


def test_empty_store_draft_returns_empty_sections_without_error(store):
    result = requirement_context(store, text="The unit shall do something useful.")
    assert result["linked"] == [] and result["peers"] == [] and result["normative"] == []
    assert result["coverage"]["has_upstream"] is False
    assert result["coverage"]["has_downstream"] is False
    assert result["coverage"]["normative_by_source"] == {"legal": 0, "standard": 0}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_requirement_context.py -v --tb=short`
Expected: the 7 new tests FAIL (empty `peers`/`normative`); the 10 earlier tests still pass.

- [ ] **Step 3: Implement**

In `smos/memory/requirement_context.py`:

1. Add to the imports: `from smos.compression.context_builder import _rrf_merge`.
2. Add the constant `_NEAR_DUPLICATE_THRESHOLD = 0.90` next to `_MAX_HOPS`.
3. Add these helpers above `requirement_context`:

```python
def _tag_set(record: dict) -> set[str]:
    raw = record.get("metadata", {}).get("tags", "") or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def _hybrid_candidates(text: str, k: int, store: VectorStore) -> list[dict]:
    """Vector + BM25 RRF-merged segment candidates, best first. Records from
    the vector side carry a cosine `score`; BM25-only hits carry `score: None`."""
    candidate_k = max(k * 8, 40)
    vector_results = store.query(text, k=candidate_k)
    bm25_results = [
        (uid, score) for uid, score in store.bm25.query(text, k=candidate_k) if score > 0.0
    ]
    if not vector_results and not bm25_results:
        return []
    merged = _rrf_merge(vector_results, bm25_results, top_k=candidate_k)
    by_id = {r["id"]: r for r in vector_results}
    candidates: list[dict] = []
    for uid, _ in merged:
        record = by_id.get(uid)
        if record is None:
            fetched = store.get_by_uuid(uid)
            if fetched is None:
                continue
            record = {**fetched, "score": None}
        candidates.append(record)
    return candidates


def _peer_items(
    store: VectorStore,
    candidates: list[dict],
    exclude_docs: set[str],
    level: str,
    domain_tags: list[str],
) -> list[dict]:
    """One best segment per requirement-like document, same level / any
    shared domain tag, never a normative document or an excluded one."""
    level_tag = f"level:{level}" if level else ""
    wanted = set(domain_tags)
    normative_tags = {f"doctype:{d}" for d in NORMATIVE_DOCTYPES}
    seen: set[str] = set()
    items: list[dict] = []
    for record in candidates:
        meta = record.get("metadata", {})
        if meta.get("type") != "segment":
            continue
        document_id = meta.get("doc_id")
        if not document_id or document_id in exclude_docs or document_id in seen:
            continue
        tags = _tag_set(record)
        if tags & normative_tags:
            continue
        if level_tag and level_tag not in tags:
            continue
        if wanted and not (wanted & tags):
            continue
        node = store.graph.get_node(document_id)
        if node is None or _placeholder(node):
            continue
        seen.add(document_id)
        score = record.get("score")
        near_duplicate = score is not None and score >= _NEAR_DUPLICATE_THRESHOLD
        items.append({
            "id": document_id,
            "doc_id": document_id,
            "segment_id": record["id"],
            "doc_name": node["name"],
            "text": record["content"],
            "score": score,
            "reason": "near duplicate" if near_duplicate else "similar requirement",
            "near_duplicate": near_duplicate,
            "hops": None,
            "path": [],
        })
    return items


def _normative_items(
    store: VectorStore,
    candidates: list[dict],
    cites: list[dict],
    normative_tags: list[str],
) -> list[dict]:
    """Explicitly cited documents first (best-matching segments, falling back
    to the document's first segment), then semantic matches from segments
    tagged doctype:legal / doctype:standard, optionally scoped by tags."""
    items: list[dict] = []
    seen_segments: set[str] = set()
    cited_docs: set[str] = set()

    for cite in cites:
        target_id = cite["target_id"]
        node = store.graph.get_node(target_id)
        if node is None or _placeholder(node) or target_id in cited_docs:
            continue
        cited_docs.add(target_id)
        own = [
            r for r in candidates
            if r.get("metadata", {}).get("doc_id") == target_id
        ][:2]
        if not own:
            first = store.get_segment_by_ordinal(target_id, 0)
            own = [{**first, "score": None}] if first else []
        for record in own:
            seen_segments.add(record["id"])
            items.append({
                "id": target_id,
                "doc_id": target_id,
                "segment_id": record["id"],
                "doc_name": node["name"],
                "text": record["content"],
                "score": record.get("score"),
                "reason": "explicit citation",
                "clause": cite.get("clause"),
                "hops": None,
                "path": [],
            })

    normative_markers = {f"doctype:{d}" for d in NORMATIVE_DOCTYPES}
    wanted = set(normative_tags)
    for record in candidates:
        meta = record.get("metadata", {})
        if meta.get("type") != "segment" or record["id"] in seen_segments:
            continue
        tags = _tag_set(record)
        kinds = [t.split(":", 1)[1] for t in tags & normative_markers]
        if not kinds:
            continue
        if wanted and not (wanted & tags):
            continue
        document_id = meta.get("doc_id")
        node = store.graph.get_node(document_id) if document_id else None
        if node is None or _placeholder(node):
            continue
        items.append({
            "id": document_id,
            "doc_id": document_id,
            "segment_id": record["id"],
            "doc_name": node["name"],
            "text": record["content"],
            "score": record.get("score"),
            "reason": f"{kinds[0]} match",
            "clause": None,
            "source_kind": kinds[0],
            "hops": None,
            "path": [],
        })
    return items
```

4. In `requirement_context`, replace the two lines

```python
    peers: list[dict] = []
    normative: list[dict] = []
```

and the final `return {...}` so that they read (keep the `query`/`linked` entries unchanged):

```python
    candidates = _hybrid_candidates(text, k, store)
    exclude_docs = {stored_id} | {i["doc_id"] for i in linked_items} if stored_id else {
        i["doc_id"] for i in linked_items
    }
    # `level:` tags select the level; only the remaining tags count as domain overlap
    peer_domain_tags = [t for t in tags if not t.startswith("level:")]
    peer_items = _peer_items(store, candidates, exclude_docs, level, peer_domain_tags)
    peers, peers_truncated = _fit(peer_items[:k], section_budget)

    normative_all = _normative_items(
        store, candidates, stored_cites + declared_cites, list(normative_tags or [])
    )
    normative, normative_truncated = _fit(normative_all[:k], section_budget)

    by_source = {d: 0 for d in NORMATIVE_DOCTYPES}
    for item in normative_all:
        kind = item.get("source_kind")
        if kind is None:
            node = graph.get_node(item["doc_id"])
            kind = (node["metadata"].get("doc_type") if node else None) or ""
        if kind in by_source:
            by_source[kind] += 1

    return {
        "query": {
            "doc_id": stored_id or None,
            "text": text,
            "level": level,
            "domain_tags": tags,
        },
        "linked": linked,
        "peers": peers,
        "normative": normative,
        "coverage": {
            "has_upstream": has_upstream,
            "has_downstream": has_downstream,
            "near_duplicates": sum(1 for p in peer_items if p["near_duplicate"]),
            "normative_by_source": by_source,
            "unresolved_links": unresolved,
        },
        "truncated": {
            "linked": linked_truncated,
            "peers": peers_truncated,
            "normative": normative_truncated,
        },
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_requirement_context.py -v --tb=short`
Expected: 17 passed. If `test_peers_flag_near_duplicates...` fails on the 0.90 threshold, print the actual `score` of R2 before touching the constant; the constant is a tunable default, but the two fixture sentences differ by one number and should score well above 0.90.

- [ ] **Step 5: Commit**

```bash
git add smos/memory/requirement_context.py tests/test_requirement_context.py
git commit -m "feat: requirement_context peers and normative sections with coverage counts"
```

---

### Task 6: MCP tool and string-argument adapter

**Files:**
- Modify: `smos/memory/requirement_context.py` (append `requirement_context_from_strings`)
- Modify: `smos/server.py` (import + one tool; place after `tool_reconstruct`)
- Test: `tests/test_requirement_context.py` (append)

**Interfaces:**
- Consumes: Task 4/5 `requirement_context`.
- Produces:
  - `requirement_context_from_strings(store, *, text="", doc_id="", system="", native_id="", level="", domain_tags="", normative_tags="", declared_links_json="", token_budget=4000, k_per_section=5) -> dict` — comma-separated tag strings, JSON list for links; malformed JSON or a non-list returns `{"error": ...}`.
  - MCP tool `tool_requirement_context` with the same parameters (module-level `_store`).

- [ ] **Step 1: Append failing tests**

Append to `tests/test_requirement_context.py`:

```python
def test_from_strings_parses_tags_and_links(store):
    from smos.memory.requirement_context import requirement_context_from_strings

    _chain(store)
    result = requirement_context_from_strings(
        store,
        text="The controller shall brake hard on impact.",
        system=SYSTEM,
        domain_tags="level:SYS.2, domain:safety",
        declared_links_json='[{"target_native_id": "S1", "edge_type": "depends_on"}]',
    )
    assert result["query"]["domain_tags"] == ["level:SYS.2", "domain:safety"]
    assert len(result["linked"]) == 1


def test_from_strings_rejects_bad_json(store):
    from smos.memory.requirement_context import requirement_context_from_strings

    result = requirement_context_from_strings(
        store, text="The unit shall run.", declared_links_json="{not json"
    )
    assert "error" in result


def test_from_strings_rejects_non_list_links(store):
    from smos.memory.requirement_context import requirement_context_from_strings

    result = requirement_context_from_strings(
        store, text="The unit shall run.", declared_links_json='{"a": 1}'
    )
    assert "error" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_requirement_context.py -k from_strings -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'requirement_context_from_strings'`.

- [ ] **Step 3: Implement**

Append to `smos/memory/requirement_context.py` (add `import json` to the imports at the top):

```python
def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def requirement_context_from_strings(
    store: VectorStore,
    *,
    text: str = "",
    doc_id: str = "",
    system: str = "",
    native_id: str = "",
    level: str = "",
    domain_tags: str = "",
    normative_tags: str = "",
    declared_links_json: str = "",
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    k_per_section: int = _DEFAULT_K,
) -> dict:
    """String-typed front door for the MCP layer: tags are comma-separated,
    declared links a JSON list of {target_native_id, edge_type[, target_system, clause]}."""
    links: Optional[list[dict]] = None
    if declared_links_json.strip():
        try:
            parsed = json.loads(declared_links_json)
        except json.JSONDecodeError as exc:
            return {"error": f"declared_links_json is not valid JSON: {exc}"}
        if not isinstance(parsed, list) or not all(isinstance(x, dict) for x in parsed):
            return {"error": "declared_links_json must be a JSON list of objects"}
        links = parsed
    return requirement_context(
        store,
        text=text,
        doc_id=doc_id,
        system=system,
        native_id=native_id,
        level=level,
        domain_tags=_split_csv(domain_tags),
        normative_tags=_split_csv(normative_tags),
        declared_links=links,
        token_budget=token_budget,
        k_per_section=k_per_section,
    )
```

In `smos/server.py`: add `from smos.memory.requirement_context import requirement_context_from_strings` next to the other `smos.memory` imports, and add after `tool_reconstruct`:

```python
@app.tool()
def tool_requirement_context(
    text: str = "",
    doc_id: str = "",
    system: str = "",
    native_id: str = "",
    level: str = "",
    domain_tags: str = "",
    normative_tags: str = "",
    declared_links_json: str = "",
    token_budget: int = 4000,
    k_per_section: int = 5,
) -> dict:
    """Gather evidence for judging a requirement: linked items, similar items,
    relevant legal/standard clauses, and coverage facts. Exact stored text, no
    LLM compression, no verdict.

    Give a draft `text`, a stored item (`doc_id`, or `system` + `native_id`), or
    both (the given text then overrides the stored text for similarity).

    Returns {"query", "linked", "peers", "normative", "coverage", "truncated"}:
      linked     items connected by depends_on/parent_of/member_of (<= 2 hops),
                 each with reason ("upstream (depends_on)", ...) and path
      peers      similar items at the same level/domain; near_duplicate flagged
      normative  legal/standard segments; explicitly cited ones come first
      coverage   has_upstream, has_downstream, near_duplicates,
                 normative_by_source, unresolved_links

    Args:
        text: Draft requirement text (optional if the item is stored).
        doc_id: Stored item's document id (alternative to system + native_id).
        system: Source system of the item; also the default namespace for
                declared link targets.
        native_id: Item id in the source system.
        level: Process level, e.g. "SYS.2" (matches tag "level:SYS.2").
        domain_tags: Comma-separated tags; peers must share at least one.
        normative_tags: Comma-separated tags scoping the normative section
                        (e.g. "iso26262"); empty = all legal/standard documents.
        declared_links_json: JSON list of links a draft intends to have, e.g.
                        [{"target_native_id": "1234", "edge_type": "depends_on"}].
        token_budget: Approximate token cap across all sections (default 4000).
        k_per_section: Max items per section (default 5).
    """
    try:
        return requirement_context_from_strings(
            _store,
            text=text,
            doc_id=doc_id,
            system=system,
            native_id=native_id,
            level=level,
            domain_tags=domain_tags,
            normative_tags=normative_tags,
            declared_links_json=declared_links_json,
            token_budget=token_budget,
            k_per_section=k_per_section,
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
```

- [ ] **Step 4: Run to verify pass, and that the server module still imports**

Run: `pytest tests/test_requirement_context.py -v --tb=short`
Expected: 20 passed.

Run: `python -I -c "import ast,sys; ast.parse(open('smos/server.py',encoding='utf-8').read()); print('server.py parses')"`
Expected: `server.py parses` (do not import the module: it opens `.smos/` in the cwd).

- [ ] **Step 5: Commit**

```bash
git add smos/memory/requirement_context.py smos/server.py tests/test_requirement_context.py
git commit -m "feat: tool_requirement_context MCP tool and string-argument adapter"
```

---

### Task 7: End-to-end fixture and full verification

**Files:**
- Create: `tests/test_requirement_context_e2e.py`

**Interfaces:**
- Consumes: everything above, through the public ingestion function and `requirement_context`.
- Produces: a regression test of the whole slice; no production code.

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_requirement_context_e2e.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.adapters.requirements import RequirementsAdapter
from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    derive_doc_id,
)
from smos.memory.requirement_context import requirement_context
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

SYSTEM = "cb-e2e"


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_adapter(RequirementsAdapter())
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _ingest(store, native_id, text, *, links=(), tags=(), doc_type="requirement", name=None):
    payload = IngestionPayload(
        external_handle=ExternalHandle(system=SYSTEM, native_id=native_id),
        doc_name=name or native_id,
        blocks=[ContentBlock(block_type="paragraph", text=text)],
        doc_type=doc_type,
        domain_tags=list(tags),
        source_links=[SourceLink(target_native_id=t, edge_type=e, metadata=m) for (t, e, m) in links],
    )
    assert do_ingest_document_schema(payload, store)["status"] == "success"


@pytest.fixture
def corpus(store: VectorStore) -> None:
    # normative sources
    _ingest(
        store, "STD-26262-6",
        "9.4.2 The software unit design shall avoid single points of failure in the "
        "brake actuation path.",
        doc_type="standard", tags=["iso26262"], name="ISO 26262-6",
    )
    _ingest(
        store, "REG-71",
        "Article 7. Manufacturers shall ensure that motor vehicle braking systems "
        "meet the prescribed stopping distances.",
        doc_type="legal", tags=["eu-law"], name="Braking Regulation",
    )
    # three-level chain
    _ingest(
        store, "SYS1-10",
        "Occupants must be protected from injury during an abrupt stop.",
        tags=["level:SYS.1", "domain:safety"], name="Occupant protection",
    )
    _ingest(
        store, "SYS2-20",
        "The brake controller shall apply full deceleration within 150 ms of a "
        "collision signal.",
        tags=["level:SYS.2", "domain:safety"], name="Collision braking",
        links=[
            ("SYS1-10", "depends_on", {}),
            ("STD-26262-6", "cites", {"clause": "9.4.2"}),
        ],
    )
    _ingest(
        store, "SYS3-30",
        "The brake actuator driver shall energise the hydraulic valve when the "
        "collision flag is set.",
        tags=["level:SYS.3", "domain:safety"], name="Valve driver",
        links=[("SYS2-20", "depends_on", {})],
    )
    # near duplicate of SYS2-20 and an unrelated item
    _ingest(
        store, "SYS2-21",
        "The brake controller shall apply full deceleration within 200 ms of a "
        "collision signal.",
        tags=["level:SYS.2", "domain:safety"], name="Collision braking (copy)",
    )
    _ingest(
        store, "SYS2-99",
        "The infotainment unit shall display the album artwork.",
        tags=["level:SYS.2", "domain:comfort"], name="Artwork",
    )


def test_stored_item_gets_all_four_sections(store, corpus):
    result = requirement_context(store, system=SYSTEM, native_id="SYS2-20")

    linked = {i["doc_id"]: i for i in result["linked"]}
    assert linked[derive_doc_id(SYSTEM, "SYS1-10")]["reason"] == "upstream (depends_on)"
    assert linked[derive_doc_id(SYSTEM, "SYS3-30")]["reason"] == "downstream (depends_on)"

    peers = {i["doc_id"]: i for i in result["peers"]}
    assert peers[derive_doc_id(SYSTEM, "SYS2-21")]["near_duplicate"] is True
    assert derive_doc_id(SYSTEM, "SYS2-99") not in peers  # different domain

    assert result["normative"][0]["doc_id"] == derive_doc_id(SYSTEM, "STD-26262-6")
    assert result["normative"][0]["reason"] == "explicit citation"
    assert result["normative"][0]["clause"] == "9.4.2"
    assert result["coverage"]["normative_by_source"]["standard"] >= 1
    assert result["coverage"]["has_upstream"] and result["coverage"]["has_downstream"]
    assert result["coverage"]["near_duplicates"] == 1


def test_linked_parent_is_returned_because_of_the_link_not_the_ranking(store, corpus):
    # A draft whose wording shares nothing with its SYS.1 parent still gets the
    # parent through its declared link; retrieval rank plays no part.
    result = requirement_context(
        store,
        text="Pedal travel beyond the end stop shall be mechanically prevented.",
        system=SYSTEM,
        declared_links=[{"target_native_id": "SYS1-10", "edge_type": "depends_on"}],
        domain_tags=["domain:safety"],
        level="SYS.2",
    )
    assert [i["doc_id"] for i in result["linked"]] == [derive_doc_id(SYSTEM, "SYS1-10")]
    assert result["linked"][0]["reason"] == "declared depends_on"


def test_draft_without_links_reports_missing_traceability(store, corpus):
    result = requirement_context(
        store,
        text="The brake controller shall apply full deceleration within 180 ms of a collision signal.",
        domain_tags=["domain:safety"],
        level="SYS.2",
    )
    assert result["linked"] == []
    assert result["coverage"]["has_upstream"] is False
    assert result["coverage"]["near_duplicates"] >= 1
    assert any(i["doc_id"] == derive_doc_id(SYSTEM, "SYS2-20") for i in result["peers"])


def test_resync_with_removed_link_changes_the_pack(store, corpus):
    _ingest(
        store, "SYS2-20",
        "The brake controller shall apply full deceleration within 150 ms of a "
        "collision signal.",
        tags=["level:SYS.2", "domain:safety"], name="Collision braking",
        links=[("STD-26262-6", "cites", {"clause": "9.4.2"})],
    )
    result = requirement_context(store, system=SYSTEM, native_id="SYS2-20")
    linked_ids = {i["doc_id"] for i in result["linked"]}
    assert derive_doc_id(SYSTEM, "SYS1-10") not in linked_ids
    assert derive_doc_id(SYSTEM, "SYS3-30") in linked_ids  # incoming link survives
    assert result["coverage"]["has_upstream"] is False
```

- [ ] **Step 2: Run the end-to-end tests**

Run: `pytest tests/test_requirement_context_e2e.py -v --tb=short`
Expected: 4 passed. Failures here point at integration gaps between Tasks 3-5; fix in the owning module, not in the test.

- [ ] **Step 3: Run the full suite and the legal bench**

Run: `pytest tests/ -q --tb=short -p no:warnings` (about 6-7 minutes; run in the background if needed)
Expected: all pass; count = previous 337 + the new tests from Tasks 1-7 (6 + 5 + 8 + 20 + 4 = 43), so 380.

Run: `python tests/bench_legal_regulations.py`
Expected: the legal-adapter dimension still reads `33% -> 100% (graph off -> on)`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_requirement_context_e2e.py
git commit -m "test: end-to-end requirements ingestion and context pack fixture"
```

- [ ] **Step 5: Orchestrator-only follow-up (not for implementers)**

Update `docs/HANDOFF.md` (new section; keep it overwrite-not-append per its header), run the `smos-doc-sync` skill, and record the new tool in `ARCHITECTURE.md`. Implementers must not edit `docs/HANDOFF.md`.

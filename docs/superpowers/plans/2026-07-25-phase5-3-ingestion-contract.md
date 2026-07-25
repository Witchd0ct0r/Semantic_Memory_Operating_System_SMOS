# Phase 5.3 — Ingestion Contract, Quarantine Validation & Resumable Checkpointing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SMOS a versioned, validated boundary (`smos/ingest/contract.py`) for external document ingestion, so a client transformer can hand SMOS a structure-preserving payload (typed blocks, not flat text) instead of a raw file, and SMOS segments/indexes/links it centrally, idempotently (external-key upsert), and resumably (checkpointed, so a mid-document crash never loses prior work or half-links the graph).

**Architecture:** A pure Pydantic contract module (`contract.py`) owns the schema, shape-validation, quarantine helpers, and the bridge that turns a payload's typed blocks into segmenter-ready structural boundaries (`ContractBlockAdapter`) — no I/O, fully unit-testable on its own, exactly like 5.2's cascade. A new orchestrator in `ingest_tools.py` (`do_ingest_document_schema`) owns the stateful parts: deterministic-id upsert branching, chunked checkpointed storage, and final containment-tree wiring. `VectorStore` gains two new SQLite tables (`ingest_quarantine`, `ingest_checkpoints`) with thin CRUD methods, following the exact pattern already used for `verbatim`/`ingested_files`. `server.py` gets one new thin MCP tool wrapper. A minimal reference transformer proves the client side of the contract end to end.

**Tech Stack:** Python, SQLite (stdlib `sqlite3`), Pydantic v2 (`>=2.5.0,<3.0.0`, confirmed installed: 2.13.4), pytest. No new dependencies — `hashlib`/`uuid`/`json` are all stdlib.

## Global Constraints

- **No new required runtime dependencies.** `hashlib`, `uuid`, `json` are stdlib; Pydantic is already a hard dependency. Consistent with the Phase 5 "zero new required deps" decision.
- **Additive schema only.** Two brand-new tables (`ingest_quarantine`, `ingest_checkpoints`) via `CREATE TABLE IF NOT EXISTS` in `VectorStore._init_db()` — no `ALTER TABLE` on `memories`, no touching the `graph_nodes`/`graph_edges` CHECK constraints (5.1/5.2 already widened those; this phase adds no new graph-node or edge types).
- **Pending-edge resolution is explicitly OUT of scope.** `IngestionPayload.source_links` is shape-validated (edge_type checked against `adapters.registry.all_edge_types()`) but SMOS does not resolve targets or create graph edges from it in this phase — that's Phase 5.5's job, once an adapter actually populates `extract_references()`. Do not build a pending-edge table or resolution pass.
- **Quarantine-inspection MCP tool is OUT of scope.** `VectorStore.get_quarantine(key)` exists as a plain method for a future session to wire up; do not add a `tool_ingest_quarantine_*` MCP tool.
- **Multi-tenancy/auth/namespacing is OUT of scope**, per the parent spec's resolved decision. Don't add any.
- **Schema simplification vs. the design spec:** the approved design doc's illustrative `IngestionPayload` pseudocode has both top-level `system`/`native_id` fields AND a nested `external_handle.system`/`external_handle.native_id` — that's redundant (two places to disagree on the same fact). This plan drops the top-level duplicates; `external_handle.system`/`external_handle.native_id` is the single source of truth used everywhere (doc_id derivation, quarantine metadata). This is a pure implementation-detail deduplication, not a behavior or scope change.
- **`build_segments()` from the design spec is renamed to `build_span_tree()` and returns the tree root (`Span`), not a flattened list.** The spec's pseudocode returned `list[Span]`, but `_wire_containment()` needs the tree structure (parent/child relationships for section grouping), not just flat leaves — a flattened-only helper would make containment wiring impossible. Callers get the root and flatten it themselves via the existing `_flatten_leaves()`.
- **Reuse of segmenter.py "private" helpers across modules is an established pattern here** — `tests/test_segmenter_cascade.py` already imports `_build_span_tree`, `_flatten_leaves`, `_merge_undersized` directly. This plan's `ingest_tools.py` does the same for `_flatten_leaves`/`_wire_containment` rather than re-exporting them under new public names, to avoid a churny rename of already-shipped, tested code.
- **MCP tool wrappers in this codebase are not unit-tested directly.** `tests/test_ingestion.py` imports and tests `do_recursive_semantic_ingest`/`do_bulk_read`/`do_semantic_snapshot_repo` from `ingest_tools.py`, never `smos.server`'s `@app.tool()` wrappers (importing `smos.server` instantiates a module-level `VectorStore` against the real `cwd()/.smos` directory — a real side effect, not something to trigger from a test run). Task 6 follows this precedent: the wrapper is a thin JSON-parse-then-delegate shim verified by code review + a clean-import smoke check, not a pytest file. All real logic is tested through `do_ingest_document_schema` directly (Task 5).
- **Test collection:** `pytest.ini` sets `python_files = ["test_*.py"]`. New tests go in `test_*.py` files under `tests/`.
- **Run tests:** `pytest tests/ -v --tb=short`. If you see an unexpected **"No tests collected"**, retry via `rtk proxy pytest tests/ -v --tb=short` before concluding anything is broken (known `rtk` hook quirk, see `docs/HANDOFF.md`).
- **Style:** `from __future__ import annotations` at the top of every new/modified module; builtin generic type hints (`list[str]`, `dict`, `Optional[...]`); no comments except where genuinely non-obvious (match the style already in every file this plan touches).
- **Scope fence:** this plan is sub-phase 5.3 only. Do not implement two-stage retrieval / `reconstruct()` (5.4), domain classification or traceability edges (5.5), graph-propagation ranking (5.6), or multilingual embeddings (5.8).

---

### Task 1: Ingestion contract schema

**Files:**
- Create: `smos/ingest/contract.py`
- Test: `tests/test_contract_schema.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ExternalHandle`, `ContentBlock`, `SourceLink`, `IngestionPayload` (Pydantic models); `derive_doc_id(system: str, native_id: str) -> str`; `content_hash(full_text: str) -> str`. Consumed by every later task in this plan.

- [ ] **Step 1: Write the failing schema tests**

Create `tests/test_contract_schema.py`:

```python
from __future__ import annotations

from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    content_hash,
    derive_doc_id,
)


def test_ingestion_payload_requires_external_handle_and_blocks():
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-1"),
        blocks=[ContentBlock(block_type="paragraph", text="Hello world.")],
    )
    assert payload.schema_version == "1.0"
    assert payload.external_handle.system == "wiki"
    assert payload.blocks[0].text == "Hello world."
    assert payload.source_links == []
    assert payload.domain_tags == []


def test_content_block_allows_optional_heading_level_and_source_anchor():
    block = ContentBlock(
        block_type="heading", text="# Title", heading_level=1, source_anchor="doc-1#h1"
    )
    assert block.heading_level == 1
    assert block.source_anchor == "doc-1#h1"


def test_source_link_carries_edge_type_and_metadata():
    link = SourceLink(target_native_id="req-42", edge_type="satisfies", metadata={"note": "x"})
    assert link.target_native_id == "req-42"
    assert link.metadata == {"note": "x"}


def test_derive_doc_id_is_deterministic_and_keyed_on_system_and_native_id():
    id_a = derive_doc_id("wiki", "doc-1")
    id_b = derive_doc_id("wiki", "doc-1")
    id_c = derive_doc_id("wiki", "doc-2")
    id_d = derive_doc_id("jira", "doc-1")
    assert id_a == id_b
    assert id_a != id_c
    assert id_a != id_d


def test_content_hash_is_deterministic_and_sensitive_to_text():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_contract_schema.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.contract'`

- [ ] **Step 3: Write the contract schema module**

Create `smos/ingest/contract.py`:

```python
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExternalHandle(BaseModel):
    system: str
    native_id: str
    deep_link: Optional[str] = None
    version: Optional[str] = None
    source_updated_at: Optional[datetime] = None


class ContentBlock(BaseModel):
    block_type: Literal["heading", "paragraph", "list", "table"]
    text: str
    heading_level: Optional[int] = None
    source_anchor: Optional[str] = None


class SourceLink(BaseModel):
    target_native_id: str
    edge_type: str
    metadata: dict = Field(default_factory=dict)


class IngestionPayload(BaseModel):
    schema_version: str = "1.0"
    external_handle: ExternalHandle
    doc_name: Optional[str] = None
    blocks: list[ContentBlock]
    doc_type: Optional[str] = None
    domain_tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    source_links: list[SourceLink] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    transformer_name: Optional[str] = None
    transformer_version: Optional[str] = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def derive_doc_id(system: str, native_id: str) -> str:
    """Deterministic id from system+native_id — re-ingesting the same source
    document always resolves to the same doc_id (idempotent upsert key)."""
    return hashlib.sha1(f"{system}:{native_id}".encode("utf-8")).hexdigest()[:16]


def content_hash(full_text: str) -> str:
    return hashlib.sha1(full_text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contract_schema.py -v --tb=short`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add smos/ingest/contract.py tests/test_contract_schema.py
git commit -m "feat: add Phase 5.3 ingestion contract schema"
```

---

### Task 2: Boundary validation & quarantine persistence

**Files:**
- Modify: `smos/ingest/contract.py` (append `validate_payload`, `quarantine_payload`)
- Modify: `smos/memory/vector_store.py:67-134` (`_init_db` — add two `CREATE TABLE IF NOT EXISTS` blocks), `smos/memory/vector_store.py:528-554` (add `save_quarantine`/`get_quarantine` before `close()`)
- Test: `tests/test_contract_validation.py`
- Test: `tests/test_vector_store_quarantine.py`

**Interfaces:**
- Consumes: `IngestionPayload`, `ExternalHandle`, `ContentBlock`, `SourceLink` (Task 1); `smos.ingest.adapters.registry.all_edge_types()` (existing, Phase 2/3); `VectorStore` (existing).
- Produces: `validate_payload(payload: IngestionPayload) -> list[str]`; `quarantine_payload(store: VectorStore, payload: IngestionPayload, errors: list[str]) -> str`; `VectorStore.save_quarantine(key, system, native_id, raw_payload, errors) -> None`; `VectorStore.get_quarantine(key) -> Optional[dict]`. Consumed by Task 5's `do_ingest_document_schema`.

- [ ] **Step 1: Write the failing VectorStore quarantine tests**

Create `tests/test_vector_store_quarantine.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_save_and_get_quarantine_round_trips(store: VectorStore) -> None:
    store.save_quarantine(
        key="q1",
        system="wiki",
        native_id="doc-1",
        raw_payload='{"external_handle": {"system": "wiki", "native_id": "doc-1"}}',
        errors='["blocks: must contain at least one block"]',
    )
    row = store.get_quarantine("q1")
    assert row is not None
    assert row["system"] == "wiki"
    assert row["native_id"] == "doc-1"
    assert "must contain at least one block" in row["errors"]


def test_get_quarantine_returns_none_for_unknown_key(store: VectorStore) -> None:
    assert store.get_quarantine("does-not-exist") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_vector_store_quarantine.py -v --tb=short`
Expected: FAIL with `AttributeError: 'VectorStore' object has no attribute 'save_quarantine'`

- [ ] **Step 3: Add the quarantine table and methods to VectorStore**

In `smos/memory/vector_store.py`, in `_init_db` (currently lines 67-134), insert a new table creation block right after the existing `ingested_files` table block (currently lines 126-132) and before `conn.commit()` (currently line 133):

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_quarantine (
                key            TEXT PRIMARY KEY,
                system         TEXT NOT NULL,
                native_id      TEXT NOT NULL,
                raw_payload    TEXT NOT NULL,
                errors         TEXT NOT NULL,
                quarantined_at TEXT NOT NULL
            )
        """)
```

Then, in the same file, add two new methods in the "File-ingestion tracking helpers" section (currently lines 528-554), right after `mark_files_ingested_batch` and before `close()` (currently line 555):

```python
    def save_quarantine(
        self, key: str, system: str, native_id: str, raw_payload: str, errors: str
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO ingest_quarantine (key, system, native_id, raw_payload, errors, quarantined_at) "
                "VALUES (?,?,?,?,?,?)",
                (key, system, native_id, raw_payload, errors, ts),
            )
            self._db.commit()

    def get_quarantine(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT key, system, native_id, raw_payload, errors, quarantined_at "
                "FROM ingest_quarantine WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "key": row[0], "system": row[1], "native_id": row[2],
            "raw_payload": row[3], "errors": row[4], "quarantined_at": row[5],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vector_store_quarantine.py -v --tb=short`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/vector_store.py tests/test_vector_store_quarantine.py
git commit -m "feat: add ingest_quarantine table and VectorStore persistence methods"
```

- [ ] **Step 6: Write the failing validation tests**

Create `tests/test_contract_validation.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.adapters.registry import register_adapter, reset_registry
from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    quarantine_payload,
    validate_payload,
)
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _valid_payload(**overrides) -> IngestionPayload:
    defaults = dict(
        external_handle=ExternalHandle(system="wiki", native_id="doc-1"),
        blocks=[ContentBlock(block_type="paragraph", text="Hello world.")],
    )
    defaults.update(overrides)
    return IngestionPayload(**defaults)


def test_valid_payload_has_no_errors():
    assert validate_payload(_valid_payload()) == []


def test_empty_blocks_list_is_rejected():
    errors = validate_payload(_valid_payload(blocks=[]))
    assert any("blocks" in e and "at least one block" in e for e in errors)


def test_blank_block_text_is_rejected():
    payload = _valid_payload(blocks=[ContentBlock(block_type="paragraph", text="   ")])
    errors = validate_payload(payload)
    assert any("text must not be empty" in e for e in errors)


def test_heading_block_without_heading_level_is_rejected():
    payload = _valid_payload(blocks=[ContentBlock(block_type="heading", text="# T")])
    errors = validate_payload(payload)
    assert any("heading_level" in e for e in errors)


def test_heading_block_with_valid_level_passes():
    payload = _valid_payload(
        blocks=[ContentBlock(block_type="heading", text="# T", heading_level=1)]
    )
    assert validate_payload(payload) == []


def test_empty_external_handle_system_is_rejected():
    payload = _valid_payload(external_handle=ExternalHandle(system="", native_id="doc-1"))
    errors = validate_payload(payload)
    assert any("external_handle.system" in e for e in errors)


def test_unknown_source_link_edge_type_is_rejected():
    payload = _valid_payload(
        source_links=[SourceLink(target_native_id="req-1", edge_type="not_a_real_type")]
    )
    errors = validate_payload(payload)
    assert any("unknown edge_type" in e for e in errors)


def test_known_core_edge_type_passes():
    payload = _valid_payload(
        source_links=[SourceLink(target_native_id="req-1", edge_type="references")]
    )
    assert validate_payload(payload) == []


def test_adapter_declared_edge_type_passes():
    class _FakeAdapter:
        name = "fake"
        edge_types = frozenset({"satisfies"})

        def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
            return []

        def extract_references(self, text: str) -> list[tuple[str, str, str]]:
            return []

        def llm_boundary_prompt(self):
            return None

    register_adapter(_FakeAdapter())
    payload = _valid_payload(
        source_links=[SourceLink(target_native_id="req-1", edge_type="satisfies")]
    )
    assert validate_payload(payload) == []


def test_quarantine_payload_persists_raw_payload_and_errors(store: VectorStore) -> None:
    payload = _valid_payload(blocks=[])
    errors = validate_payload(payload)
    key = quarantine_payload(store, payload, errors)
    row = store.get_quarantine(key)
    assert row is not None
    assert row["system"] == "wiki"
    assert row["native_id"] == "doc-1"
    assert "at least one block" in row["errors"]
```

- [ ] **Step 7: Run them to verify they fail**

Run: `pytest tests/test_contract_validation.py -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'validate_payload' from 'smos.ingest.contract'`

- [ ] **Step 8: Implement validate_payload and quarantine_payload**

Append to `smos/ingest/contract.py` (add `import json`, `import uuid`, and `from smos.ingest.adapters.registry import all_edge_types` to the imports at the top, and `from smos.memory.vector_store import VectorStore` — used only for the type hint on `quarantine_payload`):

```python
import json
import uuid

from smos.ingest.adapters.registry import all_edge_types
from smos.memory.vector_store import VectorStore

_MAX_PAYLOAD_CHARS = 5_000_000


def validate_payload(payload: IngestionPayload) -> list[str]:
    errors: list[str] = []

    if not payload.blocks:
        errors.append("blocks: must contain at least one block")

    for i, block in enumerate(payload.blocks):
        if not block.text.strip():
            errors.append(f"blocks[{i}]: text must not be empty")
        if block.block_type == "heading":
            if block.heading_level is None or not (1 <= block.heading_level <= 6):
                errors.append(f"blocks[{i}]: heading blocks require heading_level in 1..6")

    if not payload.external_handle.system.strip():
        errors.append("external_handle.system: must not be empty")
    if not payload.external_handle.native_id.strip():
        errors.append("external_handle.native_id: must not be empty")

    valid_edge_types = all_edge_types()
    for i, link in enumerate(payload.source_links):
        if link.edge_type not in valid_edge_types:
            errors.append(f"source_links[{i}]: unknown edge_type '{link.edge_type}'")

    total_chars = sum(len(b.text) for b in payload.blocks)
    if total_chars > _MAX_PAYLOAD_CHARS:
        errors.append(f"payload: total block text {total_chars} exceeds max {_MAX_PAYLOAD_CHARS}")

    return errors


def quarantine_payload(store: VectorStore, payload: IngestionPayload, errors: list[str]) -> str:
    key = str(uuid.uuid4())
    store.save_quarantine(
        key=key,
        system=payload.external_handle.system,
        native_id=payload.external_handle.native_id,
        raw_payload=payload.model_dump_json(),
        errors=json.dumps(errors),
    )
    return key
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_contract_validation.py -v --tb=short`
Expected: PASS (10 tests)

- [ ] **Step 10: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all previously-passing tests still PASS (no import cycles between `contract.py` and `vector_store.py` — `contract.py` imports `VectorStore`, and `vector_store.py` does not import anything from `smos.ingest`, so this is a one-directional import, safe).

- [ ] **Step 11: Commit**

```bash
git add smos/ingest/contract.py tests/test_contract_validation.py
git commit -m "feat: add ingestion payload boundary validation and quarantine helper"
```

---

### Task 3: Structural bridging — segmenter refactor + block-to-adapter bridge

**Files:**
- Modify: `smos/ingest/segmenter.py:172-245` (add `build_span_tree`, refactor `ingest_document_text` to use it)
- Modify: `smos/ingest/contract.py` (append `blocks_to_full_text`, `ContractBlockAdapter`, `_NoopAdapter`)
- Test: `tests/test_segmenter_cascade.py` (add coverage for `build_span_tree`)
- Test: `tests/test_contract_bridging.py`

**Interfaces:**
- Consumes: `Span`, `_flatten_leaves`, `_build_span_tree`, `_merge_undersized` (existing, `smos/ingest/segmenter.py`); `ContentBlock` (Task 1); `DomainAdapter` protocol (existing, `smos/ingest/adapters/base.py`).
- Produces: `build_span_tree(text: str, adapter: DomainAdapter) -> Span` (segmenter.py, public); `blocks_to_full_text(blocks: list[ContentBlock]) -> tuple[str, list[tuple[int, int]]]`, `ContractBlockAdapter(base_adapter, full_text, block_spans)`, `_NoopAdapter` (contract.py). Consumed by Task 5's `do_ingest_document_schema`.

- [ ] **Step 1: Write the failing segmenter test for `build_span_tree`**

Add to `tests/test_segmenter_cascade.py` (append at the end of the file):

```python
def test_build_span_tree_matches_ingest_document_text_leaves():
    from smos.ingest.segmenter import _flatten_leaves, build_span_tree

    text = "# Title\nIntro.\n## Section A\n" + ("Sentence about A. " * 60)
    root = build_span_tree(text, MarkdownAdapter())
    leaves = _flatten_leaves(root)

    assert len(leaves) >= 2
    reconstructed = "".join(text[leaf.start:leaf.end] for leaf in leaves)
    assert reconstructed == text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_segmenter_cascade.py::test_build_span_tree_matches_ingest_document_text_leaves -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'build_span_tree' from 'smos.ingest.segmenter'`

- [ ] **Step 3: Extract `build_span_tree` and refactor `ingest_document_text`**

In `smos/ingest/segmenter.py`, insert a new public function immediately before `ingest_document_text` (currently starting at line 206, right after `_wire_containment` which ends at line 203):

```python
def build_span_tree(text: str, adapter: DomainAdapter) -> Span:
    """Build the structural -> semantic -> size-fit span tree for `text` and
    merge any undersized fragments. Pure, no I/O — shared by `ingest_document_text`
    and any caller that needs to store leaves itself (e.g. checkpointed ingestion)."""
    root = _build_span_tree(text, 0, len(text), adapter)
    _merge_undersized(text, root)
    return root
```

Then replace the first three lines of `ingest_document_text`'s body (currently lines 217-219):

```python
    root = _build_span_tree(text, 0, len(text), adapter)
    _merge_undersized(text, root)
    leaves = _flatten_leaves(root)
```

with:

```python
    root = build_span_tree(text, adapter)
    leaves = _flatten_leaves(root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_segmenter_cascade.py tests/test_segmenter_containment.py -v --tb=short`
Expected: PASS — the new test passes, and every existing 5.2 test in both files still passes unchanged (this is a behavior-preserving refactor).

- [ ] **Step 5: Commit**

```bash
git add smos/ingest/segmenter.py tests/test_segmenter_cascade.py
git commit -m "refactor: extract build_span_tree from ingest_document_text"
```

- [ ] **Step 6: Write the failing bridging tests**

Create `tests/test_contract_bridging.py`:

```python
from __future__ import annotations

from smos.ingest.contract import ContentBlock, ContractBlockAdapter, _NoopAdapter, blocks_to_full_text
from smos.ingest.segmenter import _flatten_leaves, build_span_tree


def test_blocks_to_full_text_concatenates_without_separator():
    blocks = [
        ContentBlock(block_type="heading", text="# Title", heading_level=1),
        ContentBlock(block_type="paragraph", text="\nBody text.\n"),
    ]
    full_text, spans = blocks_to_full_text(blocks)
    assert full_text == "# Title\nBody text.\n"
    assert spans == [(0, 7), (7, 19)]


def test_noop_adapter_reports_no_structure_and_no_references():
    adapter = _NoopAdapter()
    assert adapter.structural_boundaries("anything") == []
    assert adapter.extract_references("anything") == []
    assert adapter.llm_boundary_prompt() is None


def test_contract_block_adapter_returns_block_spans_only_for_full_text():
    blocks = [
        ContentBlock(block_type="heading", text="# Title", heading_level=1),
        ContentBlock(block_type="paragraph", text="Body sentence here."),
    ]
    full_text, spans = blocks_to_full_text(blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, spans)

    assert adapter.structural_boundaries(full_text) == spans
    # A sub-span (as the segmenter would pass during recursion) gets no
    # further structural splitting — the block is already the finest unit.
    assert adapter.structural_boundaries(full_text[0:7]) == []


def test_contract_block_adapter_drives_span_tree_into_one_leaf_per_block():
    # Both blocks are kept well over segmenter._MIN_SEGMENT_CHARS (20) so the
    # cascade's undersized-fragment merge doesn't collapse them into one leaf
    # (that merge is exercised deliberately in test_contract_ingest.py, not here).
    blocks = [
        ContentBlock(block_type="heading", text="# A Reasonably Long Title", heading_level=1),
        ContentBlock(
            block_type="paragraph",
            text="This paragraph body is long enough to avoid the undersized-merge threshold.",
        ),
    ]
    full_text, spans = blocks_to_full_text(blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, spans)

    root = build_span_tree(full_text, adapter)
    leaves = _flatten_leaves(root)

    assert len(leaves) == 2
    assert (leaves[0].start, leaves[0].end) == spans[0]
    assert (leaves[1].start, leaves[1].end) == spans[1]
```

- [ ] **Step 7: Run them to verify they fail**

Run: `pytest tests/test_contract_bridging.py -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'ContractBlockAdapter' from 'smos.ingest.contract'`

- [ ] **Step 8: Implement blocks_to_full_text, ContractBlockAdapter, _NoopAdapter**

Append to `smos/ingest/contract.py` (add `from smos.ingest.adapters.base import DomainAdapter` to the imports at the top):

```python
from smos.ingest.adapters.base import DomainAdapter


def blocks_to_full_text(blocks: list[ContentBlock]) -> tuple[str, list[tuple[int, int]]]:
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    pos = 0
    for block in blocks:
        parts.append(block.text)
        spans.append((pos, pos + len(block.text)))
        pos += len(block.text)
    return "".join(parts), spans


class _NoopAdapter:
    name = "noop"
    edge_types: frozenset[str] = frozenset()

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None


class ContractBlockAdapter:
    """Wraps a base DomainAdapter so the segmenter's structural layer uses the
    contract's already-typed block boundaries directly instead of re-detecting
    structure via regex. Only the top-level call (whose `text` argument equals
    the entire `full_text`) returns the block spans; any recursive sub-call
    from the segmenter (operating on a single block's substring) returns no
    further structural boundaries, since a contract block is already the
    finest structural unit the client provides — the segmenter's
    semantic/size-fit layers take over from there."""

    name = "contract-block"

    def __init__(
        self,
        base_adapter: DomainAdapter,
        full_text: str,
        block_spans: list[tuple[int, int]],
    ) -> None:
        self._base = base_adapter
        self._full_text = full_text
        self._block_spans = block_spans
        self.edge_types = base_adapter.edge_types

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        if text == self._full_text:
            return self._block_spans
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return self._base.extract_references(text)

    def llm_boundary_prompt(self) -> Optional[str]:
        return self._base.llm_boundary_prompt()
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_contract_bridging.py -v --tb=short`
Expected: PASS (4 tests)

- [ ] **Step 10: Commit**

```bash
git add smos/ingest/contract.py tests/test_contract_bridging.py
git commit -m "feat: bridge contract blocks into the segmenter's structural layer"
```

---

### Task 4: VectorStore checkpoint persistence + segment lookup

**Files:**
- Modify: `smos/memory/vector_store.py:67-134` (`_init_db` — add `ingest_checkpoints` table), `smos/memory/vector_store.py` (add methods near the quarantine methods added in Task 2)
- Test: `tests/test_vector_store_checkpoint.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `VectorStore.get_segment_ids_by_doc(doc_id: str) -> list[str]`; `VectorStore.get_checkpoint(doc_id: str) -> Optional[dict]`; `VectorStore.save_checkpoint(doc_id, content_hash, total_leaves, next_leaf_index, segment_ids, status) -> None`; `VectorStore.delete_checkpoint(doc_id: str) -> None`. Consumed by Task 5's `do_ingest_document_schema`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vector_store_checkpoint.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_get_segment_ids_by_doc_returns_only_matching_doc(store: VectorStore) -> None:
    a = MemoryObject(type="segment", content="A", doc_id="doc-1", ordinal=0, char_start=0, char_end=1)
    b = MemoryObject(type="segment", content="B", doc_id="doc-1", ordinal=1, char_start=1, char_end=2)
    c = MemoryObject(type="segment", content="C", doc_id="doc-2", ordinal=0, char_start=0, char_end=1)
    ids = store.store_batch([a, b, c])

    result = store.get_segment_ids_by_doc("doc-1")
    assert sorted(result) == sorted(ids[:2])


def test_get_checkpoint_returns_none_when_absent(store: VectorStore) -> None:
    assert store.get_checkpoint("doc-x") is None


def test_save_and_get_checkpoint_round_trips(store: VectorStore) -> None:
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=5,
        next_leaf_index=2, segment_ids=["s1", "s2"], status="in_progress",
    )
    cp = store.get_checkpoint("doc-1")
    assert cp is not None
    assert cp["content_hash"] == "abc123"
    assert cp["total_leaves"] == 5
    assert cp["next_leaf_index"] == 2
    assert cp["segment_ids"] == ["s1", "s2"]
    assert cp["status"] == "in_progress"


def test_save_checkpoint_upserts_existing_row(store: VectorStore) -> None:
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=5,
        next_leaf_index=2, segment_ids=["s1", "s2"], status="in_progress",
    )
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=5,
        next_leaf_index=5, segment_ids=["s1", "s2", "s3", "s4", "s5"], status="complete",
    )
    cp = store.get_checkpoint("doc-1")
    assert cp["next_leaf_index"] == 5
    assert cp["status"] == "complete"
    assert len(cp["segment_ids"]) == 5


def test_delete_checkpoint_removes_row(store: VectorStore) -> None:
    store.save_checkpoint(
        doc_id="doc-1", content_hash="abc123", total_leaves=1,
        next_leaf_index=1, segment_ids=["s1"], status="complete",
    )
    store.delete_checkpoint("doc-1")
    assert store.get_checkpoint("doc-1") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_vector_store_checkpoint.py -v --tb=short`
Expected: FAIL with `AttributeError: 'VectorStore' object has no attribute 'get_segment_ids_by_doc'`

- [ ] **Step 3: Add the checkpoints table and methods**

In `smos/memory/vector_store.py`, in `_init_db`, add a second new table creation block immediately after the `ingest_quarantine` block added in Task 2 (still before `conn.commit()`):

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ingest_checkpoints (
                doc_id          TEXT PRIMARY KEY,
                content_hash    TEXT NOT NULL,
                total_leaves    INTEGER NOT NULL,
                next_leaf_index INTEGER NOT NULL DEFAULT 0,
                segment_ids     TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'in_progress',
                updated_at      TEXT NOT NULL
            )
        """)
```

Then add these methods right after the `get_quarantine` method added in Task 2 (still before `close()`):

```python
    def get_segment_ids_by_doc(self, doc_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT uuid FROM memories WHERE doc_id = ?", (doc_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def get_checkpoint(self, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT doc_id, content_hash, total_leaves, next_leaf_index, segment_ids, status, updated_at "
                "FROM ingest_checkpoints WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "doc_id": row[0],
            "content_hash": row[1],
            "total_leaves": row[2],
            "next_leaf_index": row[3],
            "segment_ids": [s for s in row[4].split(",") if s] if row[4] else [],
            "status": row[5],
            "updated_at": row[6],
        }

    def save_checkpoint(
        self,
        doc_id: str,
        content_hash: str,
        total_leaves: int,
        next_leaf_index: int,
        segment_ids: list[str],
        status: str,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO ingest_checkpoints "
                "(doc_id, content_hash, total_leaves, next_leaf_index, segment_ids, status, updated_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET "
                "content_hash=excluded.content_hash, total_leaves=excluded.total_leaves, "
                "next_leaf_index=excluded.next_leaf_index, segment_ids=excluded.segment_ids, "
                "status=excluded.status, updated_at=excluded.updated_at",
                (doc_id, content_hash, total_leaves, next_leaf_index, ",".join(segment_ids), status, ts),
            )
            self._db.commit()

    def delete_checkpoint(self, doc_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM ingest_checkpoints WHERE doc_id = ?", (doc_id,))
            self._db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vector_store_checkpoint.py -v --tb=short`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/memory/vector_store.py tests/test_vector_store_checkpoint.py
git commit -m "feat: add ingest_checkpoints table and VectorStore persistence methods"
```

---

### Task 5: Resumable ingestion orchestrator

**Files:**
- Modify: `smos/tools/ingest_tools.py` (add imports, `_CHECKPOINT_CHUNK_SIZE`, `delete_document_segments`, `do_ingest_document_schema`)
- Test: `tests/test_contract_ingest.py`

**Interfaces:**
- Consumes: `IngestionPayload`, `validate_payload`, `quarantine_payload`, `derive_doc_id`, `content_hash`, `blocks_to_full_text`, `ContractBlockAdapter`, `_NoopAdapter` (Tasks 1-3, `smos/ingest/contract.py`); `build_span_tree`, `_flatten_leaves`, `_wire_containment`, `Span` (`smos/ingest/segmenter.py`); `VectorStore.get_checkpoint/save_checkpoint/delete_checkpoint/get_segment_ids_by_doc` (Task 4); `VectorStore.delete_batch/store_batch` (existing); `MemoryObject` (existing); `DomainAdapter` (existing).
- Produces: `do_ingest_document_schema(payload: IngestionPayload, store: VectorStore, adapter: Optional[DomainAdapter] = None) -> dict` returning `{"status": "success"|"quarantined", ...}`; `delete_document_segments(store: VectorStore, doc_id: str) -> None`. Consumed by Task 6 (`tool_ingest_document`) and Task 7 (end-to-end tests).

- [ ] **Step 1: Write the failing orchestrator tests**

Create `tests/test_contract_ingest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import (
    ContentBlock,
    ContractBlockAdapter,
    ExternalHandle,
    IngestionPayload,
    _NoopAdapter,
    blocks_to_full_text,
    content_hash,
    derive_doc_id,
)
from smos.ingest.segmenter import _flatten_leaves, build_span_tree
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _markdown_payload(text: str, system: str, native_id: str) -> IngestionPayload:
    lines = text.splitlines(keepends=True)
    blocks: list[ContentBlock] = []
    buf = ""
    for line in lines:
        if line.startswith("#"):
            if buf:
                blocks.append(ContentBlock(block_type="paragraph", text=buf))
                buf = ""
            level = len(line) - len(line.lstrip("#"))
            blocks.append(ContentBlock(block_type="heading", text=line, heading_level=level))
        else:
            buf += line
    if buf:
        blocks.append(ContentBlock(block_type="paragraph", text=buf))
    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id), blocks=blocks
    )


def test_fresh_ingest_stores_segments_and_wires_containment(store: VectorStore) -> None:
    text = (
        "# Title\nIntro paragraph text goes here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-1")

    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    assert result["resumed"] is False
    assert result["segment_count"] == len(result["segment_ids"])
    assert result["section_count"] >= 1

    doc_node = store.graph.get_node(result["doc_id"])
    assert doc_node is not None
    assert doc_node["type"] == "document"


def test_invalid_payload_is_quarantined_not_raised(store: VectorStore) -> None:
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="bad-doc"), blocks=[]
    )
    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "quarantined"
    assert result["quarantine_key"]
    assert any("at least one block" in e for e in result["errors"])
    row = store.get_quarantine(result["quarantine_key"])
    assert row is not None


def test_resync_replaces_segments_for_same_doc_id(store: VectorStore) -> None:
    payload_v1 = _markdown_payload("# T\nOriginal body text here.\n", "wiki", "doc-sync")
    result1 = do_ingest_document_schema(payload_v1, store)
    assert result1["status"] == "success"

    payload_v2 = _markdown_payload(
        "# T\nUpdated body text, now quite different indeed.\n", "wiki", "doc-sync"
    )
    result2 = do_ingest_document_schema(payload_v2, store)
    assert result2["status"] == "success"
    assert result2["doc_id"] == result1["doc_id"]

    for old_id in result1["segment_ids"]:
        assert store.get_by_uuid(old_id) is None
    for new_id in result2["segment_ids"]:
        assert store.get_by_uuid(new_id) is not None


def test_resume_continues_from_checkpoint_without_restoring_earlier_leaves(
    store: VectorStore,
) -> None:
    text = (
        "# Title\nIntro paragraph text goes here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = _markdown_payload(text, "wiki", "doc-resume")
    doc_id = derive_doc_id("wiki", "doc-resume")
    full_text, block_spans = blocks_to_full_text(payload.blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, block_spans)
    root = build_span_tree(full_text, adapter)
    leaves = _flatten_leaves(root)
    assert len(leaves) >= 3

    partial_count = 2
    partial_memories = [
        MemoryObject(
            type="segment", content=full_text[leaf.start:leaf.end],
            doc_id=doc_id, ordinal=i, char_start=leaf.start, char_end=leaf.end,
        )
        for i, leaf in enumerate(leaves[:partial_count])
    ]
    partial_ids = store.store_batch(partial_memories)
    store.graph.add_node(doc_id, "document", doc_id)
    store.save_checkpoint(
        doc_id=doc_id, content_hash=content_hash(full_text), total_leaves=len(leaves),
        next_leaf_index=partial_count, segment_ids=partial_ids, status="in_progress",
    )

    result = do_ingest_document_schema(payload, store)

    assert result["status"] == "success"
    assert result["resumed"] is True
    assert result["segment_count"] == len(leaves)
    assert result["segment_ids"][:partial_count] == partial_ids


def test_containment_not_wired_until_ingest_completes(store: VectorStore) -> None:
    text = "# Title\nBody.\n## Section A\n" + ("More text here. " * 60)
    payload = _markdown_payload(text, "wiki", "doc-partial")
    doc_id = derive_doc_id("wiki", "doc-partial")
    full_text, block_spans = blocks_to_full_text(payload.blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, block_spans)
    root = build_span_tree(full_text, adapter)
    leaves = _flatten_leaves(root)

    store.graph.add_node(doc_id, "document", doc_id)
    store.save_checkpoint(
        doc_id=doc_id, content_hash=content_hash(full_text), total_leaves=len(leaves),
        next_leaf_index=0, segment_ids=[], status="in_progress",
    )

    neighbors = store.graph.traverse(doc_id, max_hops=1, edge_types=["contains"])
    assert neighbors == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_contract_ingest.py -v --tb=short`
Expected: FAIL with `ImportError: cannot import name 'do_ingest_document_schema' from 'smos.tools.ingest_tools'`

- [ ] **Step 3: Implement the orchestrator**

In `smos/tools/ingest_tools.py`, add to the imports at the top of the file (after the existing `from smos.memory.vector_store import VectorStore` line):

```python
from smos.ingest.adapters.base import DomainAdapter
from smos.ingest.contract import (
    ContractBlockAdapter,
    IngestionPayload,
    _NoopAdapter,
    blocks_to_full_text,
    content_hash,
    derive_doc_id,
    quarantine_payload,
    validate_payload,
)
from smos.ingest.segmenter import Span, _flatten_leaves, _wire_containment, build_span_tree
```

Then append at the end of the file (after `do_semantic_snapshot_repo`):

```python
# ─── Schema-driven ingestion (Pillar G) ───────────────────────────────────────

_CHECKPOINT_CHUNK_SIZE = 64


def delete_document_segments(store: VectorStore, doc_id: str) -> None:
    """Remove all segments and section nodes for `doc_id`, leaving the
    `document` graph node itself intact (re-sync repopulates underneath it,
    it doesn't need to be recreated)."""
    segment_ids = store.get_segment_ids_by_doc(doc_id)
    if segment_ids:
        store.delete_batch(segment_ids)
    sections = [
        n for n in store.graph.traverse(doc_id, max_hops=1, edge_types=["contains"])
        if n["type"] == "section"
    ]
    for section in sections:
        store.graph.remove_node(section["id"])


def do_ingest_document_schema(
    payload: IngestionPayload,
    store: VectorStore,
    adapter: Optional[DomainAdapter] = None,
) -> dict:
    """Validate, segment, and store a structure-preserving ingestion payload.

    Deterministic-id upsert: re-ingesting the same external_handle
    (system+native_id) resolves to the same doc_id. A prior complete ingest
    is replaced; a prior in-progress checkpoint with matching content is
    resumed from where it left off; a prior in-progress checkpoint with
    different content (client sent an update mid-flight) is discarded and
    restarted.
    """
    errors = validate_payload(payload)
    if errors:
        key = quarantine_payload(store, payload, errors)
        return {"status": "quarantined", "quarantine_key": key, "errors": errors}

    doc_id = derive_doc_id(payload.external_handle.system, payload.external_handle.native_id)
    full_text, block_spans = blocks_to_full_text(payload.blocks)
    text_hash = content_hash(full_text)

    checkpoint = store.get_checkpoint(doc_id)
    resumed = False
    if checkpoint is not None:
        if checkpoint["status"] == "complete" or checkpoint["content_hash"] != text_hash:
            delete_document_segments(store, doc_id)
            store.delete_checkpoint(doc_id)
            checkpoint = None
        else:
            resumed = True

    base_adapter = adapter or _NoopAdapter()
    block_adapter = ContractBlockAdapter(base_adapter, full_text, block_spans)
    root = build_span_tree(full_text, block_adapter)
    leaves = _flatten_leaves(root)

    store.graph.add_node(doc_id, "document", payload.doc_name or doc_id)

    start_index = checkpoint["next_leaf_index"] if checkpoint else 0
    segment_ids: list[str] = list(checkpoint["segment_ids"]) if checkpoint else []

    for leaf, sid in zip(leaves[:start_index], segment_ids):
        leaf.memory_id = sid

    remaining = leaves[start_index:]
    for offset in range(0, len(remaining), _CHECKPOINT_CHUNK_SIZE):
        chunk = remaining[offset : offset + _CHECKPOINT_CHUNK_SIZE]
        chunk_index_start = start_index + offset
        memories = [
            MemoryObject(
                type="segment",
                content=full_text[leaf.start : leaf.end],
                doc_id=doc_id,
                ordinal=chunk_index_start + i,
                char_start=leaf.start,
                char_end=leaf.end,
            )
            for i, leaf in enumerate(chunk)
        ]
        chunk_ids = store.store_batch(memories)
        for leaf, sid in zip(chunk, chunk_ids):
            leaf.memory_id = sid
        segment_ids.extend(chunk_ids)
        store.save_checkpoint(
            doc_id=doc_id,
            content_hash=text_hash,
            total_leaves=len(leaves),
            next_leaf_index=chunk_index_start + len(chunk),
            segment_ids=segment_ids,
            status="in_progress",
        )

    section_count = _wire_containment(store.graph, doc_id, root)
    store.save_checkpoint(
        doc_id=doc_id,
        content_hash=text_hash,
        total_leaves=len(leaves),
        next_leaf_index=len(leaves),
        segment_ids=segment_ids,
        status="complete",
    )

    return {
        "status": "success",
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
        "resumed": resumed,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contract_ingest.py -v --tb=short`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add smos/tools/ingest_tools.py tests/test_contract_ingest.py
git commit -m "feat: add resumable checkpointed schema-ingestion orchestrator"
```

---

### Task 6: `tool_ingest_document` MCP tool

**Files:**
- Modify: `smos/server.py:1-36` (imports), `smos/server.py:412-429` (add new tool near `tool_bulk_read`)

**Interfaces:**
- Consumes: `IngestionPayload` (Task 1), `do_ingest_document_schema` (Task 5), `pydantic.ValidationError` (existing dependency).
- Produces: `tool_ingest_document(payload_json: str) -> dict` MCP tool, registered on `app`.

- [ ] **Step 1: Add imports**

In `smos/server.py`, add to the existing `from smos.tools.ingest_tools import (...)` block (currently lines 23-27):

```python
from smos.tools.ingest_tools import (
    do_recursive_semantic_ingest,
    do_bulk_read,
    do_semantic_snapshot_repo,
    do_ingest_document_schema,
)
```

Add a new top-level import right after it:

```python
from pydantic import ValidationError

from smos.ingest.contract import IngestionPayload
```

- [ ] **Step 2: Add the tool**

In `smos/server.py`, add this new tool right after `tool_bulk_read` (currently ending at line 428, before the blank line at 429):

```python
@app.tool()
def tool_ingest_document(payload_json: str) -> dict:
    """Ingest a structure-preserving document via the versioned ingestion contract.

    Non-conforming payloads are quarantined (not rejected) with validation
    errors returned so the client can inspect and resubmit. Re-ingesting the
    same external_handle (system + native_id) is an idempotent upsert: it
    replaces a prior completed ingest, or resumes a prior interrupted one if
    the content is unchanged.

    Args:
        payload_json: JSON string matching smos.ingest.contract.IngestionPayload
                      (schema_version, external_handle{system,native_id,...},
                      blocks[{block_type,text,heading_level?,source_anchor?}], ...).
    """
    try:
        payload = IngestionPayload.model_validate_json(payload_json)
    except ValidationError as exc:
        return {"status": "error", "error": f"schema violation: {str(exc)[:500]}"}
    try:
        return do_ingest_document_schema(payload, _store)
    except Exception as exc_:
        return {"status": "error", "error": f"{type(exc_).__name__}: {str(exc_)[:300]}"}
```

- [ ] **Step 3: Verify the module imports cleanly**

Run: `python -c "import smos.server"`
Expected: no output, exit code 0 — confirms no syntax errors, import cycles, or missing names. (This codebase does not unit-test `@app.tool()` wrappers directly — see Global Constraints — because importing `smos.server` instantiates a real `VectorStore` against `cwd()/.smos`; all real logic is already covered by Task 5's tests against `do_ingest_document_schema` directly.)

- [ ] **Step 4: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add smos/server.py
git commit -m "feat: register tool_ingest_document MCP tool"
```

---

### Task 7: Reference transformer + end-to-end round-trip test

**Files:**
- Create: `smos/ingest/transformers/__init__.py`
- Create: `smos/ingest/transformers/markdown_transformer.py`
- Test: `tests/test_markdown_transformer.py`

**Interfaces:**
- Consumes: `ContentBlock`, `ExternalHandle`, `IngestionPayload`, `blocks_to_full_text` (`smos/ingest/contract.py`); `do_ingest_document_schema` (`smos/tools/ingest_tools.py`).
- Produces: `markdown_to_payload(text: str, system: str, native_id: str, doc_name: Optional[str] = None) -> IngestionPayload`. This is the last task in the plan — nothing downstream consumes it within this sub-phase.

- [ ] **Step 1: Write the failing transformer tests**

Create `smos/ingest/transformers/__init__.py` (empty file).

Create `tests/test_markdown_transformer.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import blocks_to_full_text
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_markdown_to_payload_produces_heading_and_paragraph_blocks():
    md = "# Title\nIntro paragraph text here.\n## Section A\nShort body A.\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-1")

    assert payload.external_handle.system == "wiki"
    assert payload.external_handle.native_id == "doc-1"
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "paragraph", "heading", "paragraph"]
    assert payload.blocks[0].heading_level == 1
    assert payload.blocks[2].heading_level == 2


def test_markdown_to_payload_round_trips_byte_exact():
    md = (
        "# Title\nIntro paragraph text here.\n"
        "## Section A\nShort body A.\n"
        "## Section B\nShort body B.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-2")
    full_text, _ = blocks_to_full_text(payload.blocks)
    assert full_text == md


def test_markdown_to_payload_handles_no_headings():
    md = "Just a plain paragraph with no headings at all.\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-3")
    assert len(payload.blocks) == 1
    assert payload.blocks[0].block_type == "paragraph"


def test_end_to_end_ingest_reconstructs_original_markdown(store: VectorStore) -> None:
    md = (
        "# Title\nIntro paragraph text here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-roundtrip")
    full_text, _ = blocks_to_full_text(payload.blocks)

    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "success"

    records = sorted(
        (store.get_by_uuid(sid) for sid in result["segment_ids"]),
        key=lambda r: r["metadata"]["ordinal"],
    )
    reconstructed = "".join(
        full_text[r["metadata"]["char_start"] : r["metadata"]["char_end"]] for r in records
    )
    assert reconstructed == md
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_markdown_transformer.py -v --tb=short`
Expected: FAIL with `ModuleNotFoundError: No module named 'smos.ingest.transformers.markdown_transformer'`

- [ ] **Step 3: Implement the transformer**

Create `smos/ingest/transformers/markdown_transformer.py`:

```python
from __future__ import annotations

import re
from typing import Optional

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload

_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)


def markdown_to_payload(
    text: str, system: str, native_id: str, doc_name: Optional[str] = None
) -> IngestionPayload:
    """Parse ATX headings + paragraphs into typed ContentBlocks and wrap in a
    valid IngestionPayload. Reference implementation proving the client side
    of the ingestion contract; a minimal example, not a full markdown parser
    (no list/table block typing — everything between headings becomes one
    paragraph block)."""
    matches = list(_HEADING_RE.finditer(text))
    blocks: list[ContentBlock] = []

    if not matches:
        if text.strip():
            blocks.append(ContentBlock(block_type="paragraph", text=text))
        return IngestionPayload(
            external_handle=ExternalHandle(system=system, native_id=native_id),
            doc_name=doc_name,
            blocks=blocks,
        )

    if matches[0].start() > 0:
        lead = text[: matches[0].start()]
        if lead.strip():
            blocks.append(ContentBlock(block_type="paragraph", text=lead))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        blocks.append(ContentBlock(block_type="heading", text=m.group(0), heading_level=level))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        if body.strip():
            blocks.append(ContentBlock(block_type="paragraph", text=body))

    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=doc_name,
        blocks=blocks,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_markdown_transformer.py -v --tb=short`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to confirm the whole sub-phase is green**

Run: `pytest tests/ -v --tb=short`
Expected: all tests PASS — this is the final task in the plan.

- [ ] **Step 6: Commit**

```bash
git add smos/ingest/transformers/__init__.py smos/ingest/transformers/markdown_transformer.py tests/test_markdown_transformer.py
git commit -m "feat: add minimal markdown reference transformer for the ingestion contract"
```

---

## Post-plan: HANDOFF update

Not a task with tests, but required before ending the session: update `docs/HANDOFF.md`'s "Where things stand" section to record Phase 5.3 completion (commit range, test count delta, any deviations from this plan discovered during implementation — e.g. if `ON CONFLICT` upsert syntax needed adjustment, or if the markdown transformer's whitespace-only-body edge case came up), and update the sub-phase table to mark 5.3 **DONE** and point at 5.4 as next.

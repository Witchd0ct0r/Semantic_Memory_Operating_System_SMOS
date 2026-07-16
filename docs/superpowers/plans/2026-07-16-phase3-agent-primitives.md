# Phase 3 — Agent Memory Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 8 agent-memory-primitive MCP tools (`remember`/`forget`/`compress`/`reflect`/`learn`/`link`/`snapshot`/`recall`), a `recall()` strategy dispatcher (hybrid/temporal/chain/repo), and a frequency-based tier-promotion fix, per the approved spec.

**Architecture:** New `smos/tools/agent_primitives.py` holds the primitive business logic (mirrors `semantic_tools.py`'s pattern). New `smos/memory/recall_engine.py` holds strategy dispatch, delegating the `hybrid` strategy to the existing, untouched `build_compressed_context()` so Phase 1/2's benchmark-gated retrieval code is never modified. `MemoryObject`/`memories` table gain `source_ids`/`confidence`/`recall_count`/`last_recalled`. `lifecycle.py`'s tier promotion switches from positional slicing to frequency/recency thresholds.

**Tech Stack:** Python, FastMCP, SQLite (`ALTER TABLE ADD COLUMN`), Pydantic v2, existing `smos.llm.summarizer` LLM client.

**Spec:** `docs/superpowers/specs/2026-07-16-phase3-agent-primitives-design.md` — read it for full rationale; this plan implements it task-by-task.

## Global Constraints

- All existing MCP tools remain functional and backward-compatible.
- SQLite schema changes via `ALTER TABLE ADD COLUMN` inside `except sqlite3.OperationalError: pass` only — no destructive migrations (matches the existing `tier` column pattern in `smos/memory/vector_store.py`).
- Existing `.smos/` data directories load without manual intervention — new columns must have defaults valid for pre-existing rows.
- Windows-compatible paths throughout (`pathlib.Path`).
- No new required runtime dependencies.
- `context_builder.py` is not modified by this plan — the `hybrid` recall strategy delegates to it unchanged, to avoid touching code the Phase 1 exact-term-recall and Phase 2 graph-retrieval benchmarks already gate on.
- Tier fix computes labels only — no search filtering or eviction based on tier (out of scope, tracked as follow-up debt).
- `confidence` is stored/returned only — it does not affect retrieval ranking this phase.
- `pytest.ini` sets `python_files = ["test_*.py"]` — files named `bench_*.py` are not pytest-collected; they are standalone `__main__` scripts run via `python tests/bench_x.py`.

---

## Task 1: Data model — `source_ids`/`confidence`/`recall_count`/`last_recalled`

**Files:**
- Modify: `smos/memory/schemas.py` (`MemoryObject`, currently lines 10-16)
- Modify: `smos/memory/vector_store.py` (`_init_db` lines 67-102, `store()` lines 160-194, `store_batch()` lines 372-419, `get_by_uuid()` lines 324-343)
- Test: `tests/test_vector_store_phase3_columns.py` (new)
- Test: `tests/test_schemas_phase3.py` (new)

**Interfaces:**
- Produces: `MemoryObject.source_ids: list[str]` (default `[]`), `MemoryObject.confidence: float` (default `1.0`, range `[0.0, 1.0]`)
- Produces: `VectorStore.bump_recall(uuids: list[str]) -> None` — increments `recall_count` and sets `last_recalled` to now (UTC ISO-8601) for each uuid; no-op on empty list
- Produces: `VectorStore.get_by_uuid(uuid)`'s returned `metadata` dict gains `source_ids: list[str]` and `confidence: float` keys
- Produces: `memories` table columns `source_ids TEXT`, `confidence REAL`, `recall_count INTEGER`, `last_recalled TEXT`

- [ ] **Step 1: Write the failing test for `MemoryObject` fields**

Create `tests/test_schemas_phase3.py`:

```python
from __future__ import annotations

from smos.memory.schemas import MemoryObject


def test_memory_object_defaults_source_ids_empty() -> None:
    m = MemoryObject(type="doc", content="Some content here.")
    assert m.source_ids == []


def test_memory_object_defaults_confidence_one() -> None:
    m = MemoryObject(type="doc", content="Some content here.")
    assert m.confidence == 1.0


def test_memory_object_accepts_explicit_source_ids_and_confidence() -> None:
    m = MemoryObject(
        type="compressed",
        content="Merged content.",
        source_ids=["id1", "id2"],
        confidence=0.75,
    )
    assert m.source_ids == ["id1", "id2"]
    assert m.confidence == 0.75
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_schemas_phase3.py -v`
Expected: FAIL — `AttributeError: 'MemoryObject' object has no attribute 'source_ids'` (or Pydantic silently drops the unknown kwarg, causing the third test's assertion to fail).

- [ ] **Step 3: Add the fields to `MemoryObject`**

In `smos/memory/schemas.py`, change:

```python
class MemoryObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # adr / log / doc / issue
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list)
    tier: str = Field(default="hot")  # hot / warm / cold
```

to:

```python
class MemoryObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # adr / log / doc / issue / episodic / semantic / compressed / reflection
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list)
    tier: str = Field(default="hot")  # hot / warm / cold
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_schemas_phase3.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/schemas.py tests/test_schemas_phase3.py
git commit -m "feat: add source_ids and confidence fields to MemoryObject"
```

- [ ] **Step 6: Write the failing test for persisting `source_ids`/`confidence`**

Create `tests/test_vector_store_phase3_columns.py`:

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


def _mem(content: str, **kwargs) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=datetime.utcnow(), **kwargs)


def test_store_persists_source_ids_and_confidence(store: VectorStore) -> None:
    memory = _mem("Merged summary content.", source_ids=["a", "b"], confidence=0.6)
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["source_ids"] == ["a", "b"]
    assert record["metadata"]["confidence"] == pytest.approx(0.6)


def test_store_defaults_source_ids_empty_and_confidence_one(store: VectorStore) -> None:
    memory = _mem("Plain content with no extra fields.")
    memory_id = store.store(memory)
    record = store.get_by_uuid(memory_id)
    assert record["metadata"]["source_ids"] == []
    assert record["metadata"]["confidence"] == pytest.approx(1.0)


def test_store_batch_persists_source_ids_and_confidence(store: VectorStore) -> None:
    memories = [_mem("Batch item one.", source_ids=["x"], confidence=0.9)]
    ids = store.store_batch(memories)
    record = store.get_by_uuid(ids[0])
    assert record["metadata"]["source_ids"] == ["x"]
    assert record["metadata"]["confidence"] == pytest.approx(0.9)
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `pytest tests/test_vector_store_phase3_columns.py -v`
Expected: FAIL — `KeyError: 'source_ids'` (the `metadata` dict `get_by_uuid` returns doesn't have that key yet).

- [ ] **Step 8: Add the columns and wire them through `store()`/`store_batch()`/`get_by_uuid()`**

In `smos/memory/vector_store.py`, inside `_init_db` (after the existing `tier` `ALTER TABLE`, before the `verbatim` table creation — i.e. after line 85's `pass`), add:

```python
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN source_ids TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE memories ADD COLUMN last_recalled TEXT")
        except sqlite3.OperationalError:
            pass
```

In `store()`, change the INSERT (currently lines 165-177):

```python
            cursor = self._db.execute(
                "INSERT INTO memories (uuid, type, content, timestamp, tags, tier) "
                "VALUES (?,?,?,?,?,?)",
                (
                    memory.id,
                    memory.type,
                    memory.content,
                    memory.timestamp.isoformat(),
                    ",".join(memory.tags),
                    memory.tier,
                ),
            )
```

to:

```python
            cursor = self._db.execute(
                "INSERT INTO memories (uuid, type, content, timestamp, tags, tier, source_ids, confidence) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    memory.id,
                    memory.type,
                    memory.content,
                    memory.timestamp.isoformat(),
                    ",".join(memory.tags),
                    memory.tier,
                    ",".join(memory.source_ids),
                    memory.confidence,
                ),
            )
```

Apply the identical change to the matching INSERT inside `store_batch()`'s loop (currently lines 398-409 — same SQL text, same tuple, just indented one level deeper inside `for memory in chunk:`).

In `get_by_uuid()` (currently lines 324-343), change:

```python
    def get_by_uuid(self, uuid: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT uuid, type, content, timestamp, tags, tier "
                "FROM memories WHERE uuid = ?",
                (uuid,),
            ).fetchone()
        if row is None:
            return None
        uuid_, type_, content, timestamp, tags, tier = row
        return {
            "id": uuid_,
            "content": content,
            "metadata": {
                "type": type_,
                "timestamp": timestamp,
                "tags": tags,
                "tier": tier,
            },
        }
```

to:

```python
    def get_by_uuid(self, uuid: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT uuid, type, content, timestamp, tags, tier, source_ids, confidence "
                "FROM memories WHERE uuid = ?",
                (uuid,),
            ).fetchone()
        if row is None:
            return None
        uuid_, type_, content, timestamp, tags, tier, source_ids, confidence = row
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
            },
        }
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `pytest tests/test_vector_store_phase3_columns.py -v`
Expected: PASS (3 passed)

- [ ] **Step 10: Commit**

```bash
git add smos/memory/vector_store.py tests/test_vector_store_phase3_columns.py
git commit -m "feat: persist source_ids and confidence columns in memories table"
```

- [ ] **Step 11: Write the failing test for `bump_recall`**

Append to `tests/test_vector_store_phase3_columns.py`:

```python
def test_bump_recall_increments_count_and_sets_timestamp(store: VectorStore) -> None:
    memory_id = store.store(_mem("Fact to be recalled."))
    store.bump_recall([memory_id])
    row = store._db.execute(
        "SELECT recall_count, last_recalled FROM memories WHERE uuid = ?", (memory_id,)
    ).fetchone()
    assert row[0] == 1
    assert row[1] is not None


def test_bump_recall_accumulates_across_calls(store: VectorStore) -> None:
    memory_id = store.store(_mem("Fact recalled twice."))
    store.bump_recall([memory_id])
    store.bump_recall([memory_id])
    row = store._db.execute(
        "SELECT recall_count FROM memories WHERE uuid = ?", (memory_id,)
    ).fetchone()
    assert row[0] == 2


def test_bump_recall_empty_list_is_noop(store: VectorStore) -> None:
    store.bump_recall([])  # must not raise
```

- [ ] **Step 12: Run the test to verify it fails**

Run: `pytest tests/test_vector_store_phase3_columns.py -v`
Expected: FAIL — `AttributeError: 'VectorStore' object has no attribute 'bump_recall'`

- [ ] **Step 13: Implement `bump_recall`**

In `smos/memory/vector_store.py`, add after `update_tiers_batch()` (currently ends at line 315):

```python
    def bump_recall(self, uuids: list[str]) -> None:
        if not uuids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.executemany(
                "UPDATE memories SET recall_count = recall_count + 1, last_recalled = ? WHERE uuid = ?",
                [(now, uuid) for uuid in uuids],
            )
            self._db.commit()
```

(`datetime` and `timezone` are already imported at the top of this file — no new import needed.)

- [ ] **Step 14: Run the test to verify it passes**

Run: `pytest tests/test_vector_store_phase3_columns.py -v`
Expected: PASS (6 passed)

- [ ] **Step 15: Commit**

```bash
git add smos/memory/vector_store.py tests/test_vector_store_phase3_columns.py
git commit -m "feat: add VectorStore.bump_recall for tier-tracking recall counts"
```

---

## Task 2: `agent_primitives.py` — `remember`/`forget`/`learn`/`link`/`snapshot`

**Files:**
- Create: `smos/tools/agent_primitives.py`
- Test: `tests/test_agent_primitives.py` (new)

**Interfaces:**
- Consumes: `VectorStore.store(memory: MemoryObject) -> str`, `VectorStore.delete(uuid: str) -> bool`, `VectorStore.graph.get_node(node_id) -> Optional[dict]`, `VectorStore.graph.add_edge(source_id, target_id, edge_type)`, `do_semantic_snapshot_repo(path: str, store: VectorStore) -> dict` (from `smos.tools.ingest_tools`)
- Produces: `remember(content: str, type: str, store: VectorStore) -> str`, `forget(memory_id: str, store: VectorStore) -> bool`, `learn(content: str, store: VectorStore) -> str`, `link(id_a: str, id_b: str, edge_type: str, store: VectorStore) -> dict`, `snapshot(project_root: str, store: VectorStore) -> dict`
- Produces: `_validate_text(text: str) -> bool` and `_VALID_EDGE_TYPES: set[str]` module-level helpers, reused by later tasks in this same file

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_primitives.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from smos.memory.schemas import MemoryObject
from smos.tools.agent_primitives import forget, learn, link, remember, snapshot


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.store.return_value = "mock-id-001"
    return store


def test_remember_calls_store(mock_store: MagicMock) -> None:
    result = remember("The service uses Redis for caching.", "episodic", mock_store)
    assert result == "mock-id-001"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "episodic"
    assert call_arg.content == "The service uses Redis for caching."


def test_remember_rejects_short_content(mock_store: MagicMock) -> None:
    result = remember("short", "episodic", mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_forget_calls_delete(mock_store: MagicMock) -> None:
    mock_store.delete.return_value = True
    result = forget("some-id", mock_store)
    assert result is True
    mock_store.delete.assert_called_once_with("some-id")


def test_learn_sets_semantic_type_and_confidence(mock_store: MagicMock) -> None:
    result = learn("PostgreSQL uses MVCC for isolation.", mock_store)
    assert result == "mock-id-001"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "semantic"
    assert call_arg.confidence == 1.0


def test_learn_rejects_short_content(mock_store: MagicMock) -> None:
    result = learn("short", mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_link_rejects_invalid_edge_type(mock_store: MagicMock) -> None:
    result = link("id_a", "id_b", "not_a_real_type", mock_store)
    assert result["success"] is False
    mock_store.graph.add_edge.assert_not_called()


def test_link_rejects_missing_nodes(mock_store: MagicMock) -> None:
    mock_store.graph.get_node.return_value = None
    result = link("id_a", "id_b", "references", mock_store)
    assert result["success"] is False
    mock_store.graph.add_edge.assert_not_called()


def test_link_adds_edge_for_valid_nodes(mock_store: MagicMock) -> None:
    mock_store.graph.get_node.return_value = {"id": "id_a", "type": "memory"}
    result = link("id_a", "id_b", "references", mock_store)
    assert result["success"] is True
    mock_store.graph.add_edge.assert_called_once_with("id_a", "id_b", "references")


def test_snapshot_delegates_to_ingest(mock_store: MagicMock) -> None:
    with patch(
        "smos.tools.agent_primitives.do_semantic_snapshot_repo",
        return_value={"status": "ok"},
    ) as mock_snap:
        result = snapshot("/repo/root", mock_store)
    assert result == {"status": "ok"}
    mock_snap.assert_called_once_with("/repo/root", mock_store)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_agent_primitives.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smos.tools.agent_primitives'`

- [ ] **Step 3: Implement `agent_primitives.py`**

Create `smos/tools/agent_primitives.py`:

```python
from __future__ import annotations

from datetime import datetime

from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_semantic_snapshot_repo

_MIN_INPUT_CHARS = 10
_VALID_EDGE_TYPES = {
    "references", "depends_on", "related_to",
    "implements", "contains", "evolves_into", "generated_from",
}


def _validate_text(text: str) -> bool:
    return bool(text) and len(text.strip()) >= _MIN_INPUT_CHARS


def remember(content: str, type: str, store: VectorStore) -> str:
    if not _validate_text(content):
        return ""
    memory = MemoryObject(type=type, content=content, timestamp=datetime.utcnow())
    return store.store(memory)


def forget(memory_id: str, store: VectorStore) -> bool:
    return store.delete(memory_id)


def learn(content: str, store: VectorStore) -> str:
    if not _validate_text(content):
        return ""
    memory = MemoryObject(
        type="semantic", content=content, timestamp=datetime.utcnow(), confidence=1.0
    )
    return store.store(memory)


def link(id_a: str, id_b: str, edge_type: str, store: VectorStore) -> dict:
    if edge_type not in _VALID_EDGE_TYPES:
        return {"success": False, "error": f"invalid edge_type: {edge_type}"}
    if store.graph.get_node(id_a) is None or store.graph.get_node(id_b) is None:
        return {"success": False, "error": "one or both ids do not exist as graph nodes"}
    store.graph.add_edge(id_a, id_b, edge_type)
    return {"success": True, "error": None}


def snapshot(project_root: str, store: VectorStore) -> dict:
    return do_semantic_snapshot_repo(project_root, store)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_agent_primitives.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/tools/agent_primitives.py tests/test_agent_primitives.py
git commit -m "feat: add remember/forget/learn/link/snapshot agent primitives"
```

---

## Task 3: `compress()` primitive

**Files:**
- Modify: `smos/tools/agent_primitives.py`
- Test: `tests/test_agent_primitives.py`

**Interfaces:**
- Consumes: `VectorStore.get_by_uuid(uuid) -> Optional[dict]`, `VectorStore.store(memory) -> str`, `VectorStore.graph.add_edge(source_id, target_id, edge_type)`, `compress_memories_full(memories: list[dict], query: str) -> tuple[str, float, str]` (from `smos.llm.summarizer`, existing — see `smos/llm/summarizer.py:81`)
- Produces: `compress(ids: list[str], store: VectorStore) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_primitives.py`, and change the import line at the top from:

```python
from smos.tools.agent_primitives import forget, learn, link, remember, snapshot
```

to:

```python
from smos.tools.agent_primitives import compress, forget, learn, link, remember, snapshot
```

Then append:

```python
def test_compress_merges_sources_and_links_edges(mock_store: MagicMock) -> None:
    mock_store.get_by_uuid.side_effect = lambda uid: {
        "id": uid,
        "content": f"content for {uid}",
        "metadata": {"type": "doc", "timestamp": "t", "tags": "", "tier": "hot",
                     "source_ids": [], "confidence": 1.0},
    }
    mock_store.store.return_value = "compressed-id-1"
    with patch(
        "smos.tools.agent_primitives.compress_memories_full",
        return_value=("Merged summary.", 0.8, "abstractive"),
    ):
        result = compress(["id1", "id2"], mock_store)

    assert result == "compressed-id-1"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "compressed"
    assert call_arg.content == "Merged summary."
    assert call_arg.source_ids == ["id1", "id2"]
    assert call_arg.confidence == 0.8
    assert mock_store.graph.add_edge.call_count == 2
    mock_store.graph.add_edge.assert_any_call("compressed-id-1", "id1", "generated_from")
    mock_store.graph.add_edge.assert_any_call("compressed-id-1", "id2", "generated_from")


def test_compress_skips_missing_sources(mock_store: MagicMock) -> None:
    mock_store.get_by_uuid.side_effect = lambda uid: (
        None if uid == "missing" else {"id": uid, "content": "real content", "metadata": {}}
    )
    mock_store.store.return_value = "compressed-id-2"
    with patch(
        "smos.tools.agent_primitives.compress_memories_full",
        return_value=("Summary.", 0.5, "abstractive"),
    ):
        result = compress(["missing", "id2"], mock_store)
    assert result == "compressed-id-2"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.source_ids == ["id2"]


def test_compress_empty_ids_returns_empty(mock_store: MagicMock) -> None:
    result = compress([], mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_compress_all_sources_missing_returns_empty(mock_store: MagicMock) -> None:
    mock_store.get_by_uuid.return_value = None
    result = compress(["missing1", "missing2"], mock_store)
    assert result == ""
    mock_store.store.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_agent_primitives.py -v`
Expected: FAIL — `ImportError: cannot import name 'compress' from 'smos.tools.agent_primitives'`

- [ ] **Step 3: Implement `compress()`**

In `smos/tools/agent_primitives.py`, add the import:

```python
from smos.llm.summarizer import compress_memories_full
```

and add the function:

```python
_COMPRESS_MERGE_QUERY = "Merge these memory fragments into one dense, accurate summary."


def compress(ids: list[str], store: VectorStore) -> str:
    if not ids:
        return ""
    sources = [s for s in (store.get_by_uuid(i) for i in ids) if s is not None]
    if not sources:
        return ""

    memories_for_compression = [
        {"id": s["id"], "content": s["content"], "distance": 1.0} for s in sources
    ]
    summary, confidence, _mode = compress_memories_full(memories_for_compression, _COMPRESS_MERGE_QUERY)

    memory = MemoryObject(
        type="compressed",
        content=summary,
        timestamp=datetime.utcnow(),
        source_ids=[s["id"] for s in sources],
        confidence=confidence,
    )
    new_id = store.store(memory)
    for source_id in memory.source_ids:
        store.graph.add_edge(new_id, source_id, "generated_from")
    return new_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_agent_primitives.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/tools/agent_primitives.py tests/test_agent_primitives.py
git commit -m "feat: add compress() primitive — non-destructive memory merge"
```

---

## Task 4: `reflect()` primitive + distinct LLM prompt

**Files:**
- Modify: `smos/llm/summarizer.py`
- Modify: `smos/tools/agent_primitives.py`
- Test: `tests/test_summarizer.py`
- Test: `tests/test_agent_primitives.py`

**Interfaces:**
- Consumes: `VectorStore.query_domain(query_text, k, domain_tags=None) -> list[dict]` (existing, `smos/memory/vector_store.py:205`), `VectorStore.query(query_text, k) -> list[dict]` (existing), `get_llm_client()`/`OLLAMA_MODEL`/`_MAX_TOKENS`/`_is_valid_output`/`_extractive_fallback_from_memories` (existing module-level helpers in `smos/llm/summarizer.py`)
- Produces: `reflect_on_memories(memories: list[dict], query: str) -> tuple[str, float, str]` in `smos/llm/summarizer.py`
- Produces: `reflect(query: str, k: int, store: VectorStore) -> str` in `smos/tools/agent_primitives.py`

- [ ] **Step 1: Write the failing tests for `reflect_on_memories`**

In `tests/test_summarizer.py`, change the import line from:

```python
from smos.llm.summarizer import compress_memories, summarize_text
```

to:

```python
from smos.llm.summarizer import compress_memories, reflect_on_memories, summarize_text
```

Append:

```python
def test_reflect_on_memories_returns_reflection_and_confidence() -> None:
    memories = [
        {"id": "abc12345", "content": "PostgreSQL uses MVCC.", "distance": 0.1},
        {"id": "def67890", "content": "Redis is single-threaded.", "distance": 0.3},
    ]
    mock_client = _make_mock_client("Both are used for different storage needs; no contradiction found.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        reflection, confidence, mode = reflect_on_memories(memories, "what do I know about storage?")
    assert isinstance(reflection, str)
    assert len(reflection) > 0
    assert 0.0 <= confidence <= 1.0
    assert mode == "abstractive"


def test_reflect_on_memories_empty_returns_zero() -> None:
    reflection, confidence, mode = reflect_on_memories([], "anything")
    assert reflection == ""
    assert confidence == 0.0
    assert mode == "uncertain"


def test_reflect_on_memories_uses_distinct_system_prompt() -> None:
    memories = [{"id": "aaa00000", "content": "Some fact.", "distance": 0.2}]
    mock_client = _make_mock_client("Reflection output.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        reflect_on_memories(memories, "query")
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    system_msg = call_kwargs["messages"][0]["content"]
    assert "reflecting" in system_msg.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_summarizer.py -v`
Expected: FAIL — `ImportError: cannot import name 'reflect_on_memories' from 'smos.llm.summarizer'`

- [ ] **Step 3: Implement `reflect_on_memories`**

In `smos/llm/summarizer.py`, add after `_COMPRESS_SYSTEM_STRICT` (currently line 25):

```python
_REFLECT_SYSTEM = (
    "You are an agent reflecting on your own accumulated memory. "
    "You receive multiple retrieved memory fragments and a query about what you know. "
    "Reason about the fragments: note what is established, what gaps remain, "
    "and any contradictions between fragments. "
    "Output only the reflection — no preamble, no metadata."
)
```

Add the function after `compress_memories_full` (currently ends at line 122, before `compress_memories`):

```python
def reflect_on_memories(memories: list[dict], query: str) -> tuple[str, float, str]:
    """Return (reflection, confidence, mode). Distinct prompt from compress_memories_full —
    reasons about the fragments rather than summarizing them."""
    if not memories:
        return "", 0.0, "uncertain"

    sections = [f"[{m['id'][:8]}] {m['content']}" for m in memories]
    combined = "\n\n".join(sections)
    user_content = f"Query: {query}\n\nMemory fragments:\n{combined}"

    client = get_llm_client()

    def _call() -> str:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _REFLECT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()

    try:
        reflection = _call()
        if not _is_valid_output(reflection):
            reflection = _call()
        if not _is_valid_output(reflection):
            reflection = _extractive_fallback_from_memories(memories)
            mode: str = "extractive"
        else:
            mode = "abstractive"
    except Exception:
        reflection = _extractive_fallback_from_memories(memories)
        mode = "uncertain"

    avg_distance = sum(m["distance"] for m in memories) / len(memories)
    confidence = max(0.0, min(1.0, 1.0 - (avg_distance / 2.0)))

    return reflection, confidence, mode
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_summarizer.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/llm/summarizer.py tests/test_summarizer.py
git commit -m "feat: add reflect_on_memories — distinct reasoning prompt for self-reflection"
```

- [ ] **Step 6: Write the failing tests for `reflect()`**

In `tests/test_agent_primitives.py`, change the import line to:

```python
from smos.tools.agent_primitives import compress, forget, learn, link, reflect, remember, snapshot
```

Append:

```python
def test_reflect_stores_reflection_with_source_ids(mock_store: MagicMock) -> None:
    mock_store.query_domain.return_value = [
        {"id": "id1", "content": "fact one", "distance": 0.1},
        {"id": "id2", "content": "fact two", "distance": 0.2},
    ]
    mock_store.store.return_value = "reflection-id-1"
    with patch(
        "smos.tools.agent_primitives.reflect_on_memories",
        return_value=("Reflection text.", 0.7, "abstractive"),
    ):
        result = reflect("what do I know about X?", 5, mock_store)
    assert result == "reflection-id-1"
    call_arg: MemoryObject = mock_store.store.call_args[0][0]
    assert call_arg.type == "reflection"
    assert call_arg.source_ids == ["id1", "id2"]
    assert call_arg.confidence == 0.7


def test_reflect_no_candidates_returns_empty(mock_store: MagicMock) -> None:
    mock_store.query_domain.return_value = []
    mock_store.query.return_value = []
    result = reflect("unknown topic entirely", 5, mock_store)
    assert result == ""
    mock_store.store.assert_not_called()


def test_reflect_rejects_short_query(mock_store: MagicMock) -> None:
    result = reflect("hi", 5, mock_store)
    assert result == ""
    mock_store.query_domain.assert_not_called()
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `pytest tests/test_agent_primitives.py -v`
Expected: FAIL — `ImportError: cannot import name 'reflect' from 'smos.tools.agent_primitives'`

- [ ] **Step 8: Implement `reflect()`**

In `smos/tools/agent_primitives.py`, add the import:

```python
from smos.llm.summarizer import compress_memories_full, reflect_on_memories
```

(replacing the single-name import added in Task 3) and add the function:

```python
def reflect(query: str, k: int, store: VectorStore) -> str:
    if not _validate_text(query):
        return ""
    # Vector-only candidates — full RRF hybrid fusion isn't needed for this
    # introspective use case, and avoids depending on context_builder.py's
    # tested-and-gated internals.
    candidates = store.query_domain(query, k=k)
    if not candidates:
        candidates = store.query(query, k=k)
    if not candidates:
        return ""

    memories_for_reflection = [
        {"id": c["id"], "content": c["content"], "distance": c.get("distance", 1.0)}
        for c in candidates
    ]
    reflection, confidence, _mode = reflect_on_memories(memories_for_reflection, query)

    memory = MemoryObject(
        type="reflection",
        content=reflection,
        timestamp=datetime.utcnow(),
        source_ids=[c["id"] for c in candidates],
        confidence=confidence,
    )
    return store.store(memory)
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `pytest tests/test_agent_primitives.py -v`
Expected: PASS (16 passed)

- [ ] **Step 10: Commit**

```bash
git add smos/tools/agent_primitives.py tests/test_agent_primitives.py
git commit -m "feat: add reflect() primitive"
```

---

## Task 5: `recall_engine.py` — hybrid/temporal/chain/repo strategy dispatch

**Files:**
- Create: `smos/memory/recall_engine.py`
- Modify: `smos/memory/vector_store.py` (add `get_by_timestamp_range`)
- Test: `tests/test_recall_engine.py` (new)

**Interfaces:**
- Consumes: `build_compressed_context(query, k, store, tags=None) -> CompressedContext` (existing, `smos/compression/context_builder.py:44`), `VectorStore.graph.traverse(start_id, max_hops, edge_types=None) -> list[dict]` (existing, `smos/memory/graph_store.py:112`), `VectorStore.bump_recall(uuids)` (Task 1), `embed_batch(texts: list[str]) -> list[list[float]]` (existing, `smos/memory/embeddings.py`), `compress_memories_full(memories, query) -> tuple[str, float, str]` (existing)
- Produces: `VectorStore.get_by_timestamp_range(since: str = "", until: str = "") -> list[dict]` — each dict is `{"id": str, "content": str}`
- Produces: `recall(query: str, strategy: str, k: int, since: str, until: str, seed_id: str, component: str, store: VectorStore) -> dict` in `smos/memory/recall_engine.py`. Returns `{"summary", "sources", "confidence", "mode"}` for `hybrid`/`temporal`/`repo`, or `{"sources", "chain", "confidence", "mode"}` for `chain`, or `{"error": str}` for an unrecognized strategy.

- [ ] **Step 1: Write the failing test for `get_by_timestamp_range`**

Create `tests/test_recall_engine.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smos.memory.recall_engine import recall
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str, timestamp: datetime | None = None, **kwargs) -> MemoryObject:
    return MemoryObject(type="doc", content=content, timestamp=timestamp or datetime.utcnow(), **kwargs)


def test_get_by_timestamp_range_filters_correctly(store: VectorStore) -> None:
    old_id = store.store(_mem("Old fact.", timestamp=datetime.utcnow() - timedelta(days=10)))
    new_id = store.store(_mem("New fact.", timestamp=datetime.utcnow()))
    since = (datetime.utcnow() - timedelta(days=1)).isoformat()
    rows = store.get_by_timestamp_range(since=since)
    ids = {r["id"] for r in rows}
    assert new_id in ids
    assert old_id not in ids


def test_get_by_timestamp_range_unbounded_returns_all(store: VectorStore) -> None:
    store.store(_mem("Fact one."))
    store.store(_mem("Fact two."))
    rows = store.get_by_timestamp_range()
    assert len(rows) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_recall_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'smos.memory.recall_engine'`

- [ ] **Step 3: Add `get_by_timestamp_range` to `VectorStore`**

In `smos/memory/vector_store.py`, add after `get_by_uuid()` (after Task 1's edit, still before `count()`):

```python
    def get_by_timestamp_range(self, since: str = "", until: str = "") -> list[dict]:
        sql = "SELECT uuid, content FROM memories WHERE 1=1"
        params: list[str] = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until:
            sql += " AND timestamp <= ?"
            params.append(until)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [{"id": r[0], "content": r[1]} for r in rows]
```

- [ ] **Step 4: Create a minimal `recall_engine.py` so imports resolve, run the timestamp test**

Create `smos/memory/recall_engine.py` with just enough for `tests/test_recall_engine.py`'s import to succeed (full dispatcher is filled in over the remaining steps):

```python
from __future__ import annotations

from smos.compression.context_builder import build_compressed_context
from smos.memory.vector_store import VectorStore

_VALID_STRATEGIES = {"hybrid", "temporal", "chain", "repo"}


def recall(
    query: str,
    strategy: str,
    k: int,
    since: str,
    until: str,
    seed_id: str,
    component: str,
    store: VectorStore,
) -> dict:
    if strategy not in _VALID_STRATEGIES:
        return {"error": f"unknown strategy: {strategy}"}
    result = build_compressed_context(query, k, store).model_dump()
    store.bump_recall(result.get("sources", []))
    return result
```

Run: `pytest tests/test_recall_engine.py -v`
Expected: PASS (2 passed — the timestamp-range tests only touch `VectorStore`, not `recall()` yet)

- [ ] **Step 5: Commit**

```bash
git add smos/memory/vector_store.py smos/memory/recall_engine.py tests/test_recall_engine.py
git commit -m "feat: add get_by_timestamp_range and recall_engine skeleton"
```

- [ ] **Step 6: Write the failing tests for the `recall()` dispatcher — unknown strategy and hybrid**

Append to `tests/test_recall_engine.py`:

```python
def test_recall_unknown_strategy_returns_error(store: VectorStore) -> None:
    result = recall("query", "bogus", 5, "", "", "", "", store)
    assert result == {"error": "unknown strategy: bogus"}


def test_recall_hybrid_matches_build_compressed_context(store: VectorStore) -> None:
    store.store(_mem("PostgreSQL uses MVCC for isolation."))
    from smos.compression.context_builder import build_compressed_context
    direct = build_compressed_context("database isolation", 5, store).model_dump()
    via_recall = recall("database isolation", "hybrid", 5, "", "", "", "", store)
    assert via_recall["summary"] == direct["summary"]
    assert via_recall["sources"] == direct["sources"]


def test_recall_bumps_recall_count(store: VectorStore) -> None:
    mem_id = store.store(_mem("PostgreSQL uses MVCC for isolation."))
    recall("database isolation", "hybrid", 5, "", "", "", "", store)
    row = store._db.execute(
        "SELECT recall_count FROM memories WHERE uuid = ?", (mem_id,)
    ).fetchone()
    assert row[0] == 1
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_recall_engine.py -v`
Expected: PASS (5 passed) — Step 4's skeleton already implements `hybrid` correctly and calls `bump_recall`, so these should pass without further changes. If `test_recall_unknown_strategy_returns_error` fails, double check the skeleton's early-return branch from Step 4.

- [ ] **Step 8: Commit**

```bash
git add tests/test_recall_engine.py
git commit -m "test: cover recall() hybrid strategy and unknown-strategy handling"
```

- [ ] **Step 9: Write the failing test for the `repo` strategy**

Append to `tests/test_recall_engine.py`:

```python
def test_recall_repo_filters_by_component(store: VectorStore) -> None:
    auth_id = store.store(
        MemoryObject(type="doc", content="Auth service handles login flows for users.", tags=["auth-service"])
    )
    billing_id = store.store(
        MemoryObject(type="doc", content="Billing service handles invoices for customers.", tags=["billing-service"])
    )
    result = recall("service behavior", "repo", 5, "", "", "", "auth-service", store)
    assert auth_id in result["sources"]
    assert billing_id not in result["sources"]
```

- [ ] **Step 10: Run the test to verify it fails**

Run: `pytest tests/test_recall_engine.py -v`
Expected: FAIL — the skeleton's `else` branch ignores `strategy == "repo"` and calls `build_compressed_context` without `tags`, so both memories are returned and `billing_id in result["sources"]` makes the assertion fail.

- [ ] **Step 11: Implement the `repo` branch**

Replace the body of `recall()` in `smos/memory/recall_engine.py` with:

```python
def recall(
    query: str,
    strategy: str,
    k: int,
    since: str,
    until: str,
    seed_id: str,
    component: str,
    store: VectorStore,
) -> dict:
    if strategy not in _VALID_STRATEGIES:
        return {"error": f"unknown strategy: {strategy}"}

    if strategy == "repo":
        result = _recall_repo(query, k, component, store)
    else:
        result = build_compressed_context(query, k, store).model_dump()

    store.bump_recall(result.get("sources", []))
    return result


def _recall_repo(query: str, k: int, component: str, store: VectorStore) -> dict:
    tags = [component] if component else None
    return build_compressed_context(query, k, store, tags=tags).model_dump()
```

- [ ] **Step 12: Run the tests to verify they pass**

Run: `pytest tests/test_recall_engine.py -v`
Expected: PASS (6 passed)

- [ ] **Step 13: Commit**

```bash
git add smos/memory/recall_engine.py tests/test_recall_engine.py
git commit -m "feat: add repo strategy to recall()"
```

- [ ] **Step 14: Write the failing test for the `temporal` strategy**

Append to `tests/test_recall_engine.py`:

```python
def test_recall_temporal_respects_since_bound(store: VectorStore) -> None:
    old_id = store.store(_mem("Old fact about caching.", timestamp=datetime.utcnow() - timedelta(days=10)))
    new_id = store.store(_mem("New fact about caching.", timestamp=datetime.utcnow()))
    since = (datetime.utcnow() - timedelta(days=1)).isoformat()
    result = recall("caching", "temporal", 5, since, "", "", "", store)
    assert new_id in result["sources"]
    assert old_id not in result["sources"]


def test_recall_temporal_no_matches_returns_uncertain(store: VectorStore) -> None:
    since = (datetime.utcnow() + timedelta(days=1)).isoformat()  # future — nothing qualifies
    result = recall("anything", "temporal", 5, since, "", "", "", store)
    assert result["sources"] == []
    assert result["mode"] == "uncertain"
```

- [ ] **Step 15: Run the tests to verify they fail**

Run: `pytest tests/test_recall_engine.py -v`
Expected: FAIL — `strategy == "temporal"` falls into the `else` branch, ignoring `since`/`until` entirely, so `old_id` is incorrectly included.

- [ ] **Step 16: Implement the `temporal` branch**

In `smos/memory/recall_engine.py`, add imports at the top:

```python
import numpy as np

from smos.llm.summarizer import compress_memories_full
from smos.memory.embeddings import embed_batch
```

Update the dispatcher and add `_recall_temporal`:

```python
    if strategy == "repo":
        result = _recall_repo(query, k, component, store)
    elif strategy == "temporal":
        result = _recall_temporal(query, k, since, until, store)
    else:
        result = build_compressed_context(query, k, store).model_dump()
```

```python
def _recall_temporal(query: str, k: int, since: str, until: str, store: VectorStore) -> dict:
    rows = store.get_by_timestamp_range(since, until)
    if not rows:
        return {"summary": "No relevant memories found.", "sources": [], "confidence": 0.0, "mode": "uncertain"}

    contents = [r["content"] for r in rows]
    vecs = np.array(embed_batch([query] + contents), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs /= norms
    query_vec, content_vecs = vecs[0], vecs[1:]
    scores = content_vecs @ query_vec

    scored = sorted(zip(rows, scores), key=lambda t: t[1], reverse=True)[:k]

    memories_for_compression = [
        {"id": row["id"], "content": row["content"], "distance": float(1.0 - score)}
        for row, score in scored
    ]
    summary, confidence, mode = compress_memories_full(memories_for_compression, query)
    return {
        "summary": summary,
        "sources": [row["id"] for row, _ in scored],
        "confidence": confidence,
        "mode": mode,
    }
```

- [ ] **Step 17: Run the tests to verify they pass**

Run: `pytest tests/test_recall_engine.py -v`
Expected: PASS (8 passed)

- [ ] **Step 18: Commit**

```bash
git add smos/memory/recall_engine.py tests/test_recall_engine.py
git commit -m "feat: add temporal strategy to recall()"
```

- [ ] **Step 19: Write the failing tests for the `chain` strategy**

Append to `tests/test_recall_engine.py`:

```python
def test_recall_chain_follows_evolves_into_edges(store: VectorStore) -> None:
    m1 = store.store(_mem("Version one of the design doc."))
    m2 = store.store(_mem("Version two of the design doc."))
    store.graph.add_edge(m1, m2, "evolves_into")
    result = recall("", "chain", 2, "", "", m1, "", store)
    assert m2 in result["sources"]
    assert result["mode"] == "extractive"


def test_recall_chain_ignores_references_edges(store: VectorStore) -> None:
    m1 = store.store(_mem("The `Foo` class is defined here."))
    result = recall("", "chain", 2, "", "", m1, "", store)
    assert result["sources"] == []
```

- [ ] **Step 20: Run the tests to verify they fail**

Run: `pytest tests/test_recall_engine.py -v`
Expected: FAIL — `strategy == "chain"` falls into the `else` branch and calls `build_compressed_context("", 2, store)`, which does not return the shape these tests expect (no `"chain"` key, and would attempt hybrid retrieval on an empty query).

- [ ] **Step 21: Implement the `chain` branch**

In `smos/memory/recall_engine.py`, add the module constant near `_VALID_STRATEGIES`:

```python
_CHAIN_EDGE_TYPES = ["evolves_into", "related_to"]
```

Update the dispatcher:

```python
    if strategy == "repo":
        result = _recall_repo(query, k, component, store)
    elif strategy == "temporal":
        result = _recall_temporal(query, k, since, until, store)
    elif strategy == "chain":
        result = _recall_chain(seed_id, k, store)
    else:
        result = build_compressed_context(query, k, store).model_dump()
```

Add the function:

```python
def _recall_chain(seed_id: str, k: int, store: VectorStore) -> dict:
    nodes = store.graph.traverse(seed_id, max_hops=max(k, 1), edge_types=_CHAIN_EDGE_TYPES)
    memory_ids = [n["id"] for n in nodes if n["type"] == "memory"]
    return {"sources": memory_ids, "chain": nodes, "confidence": 1.0, "mode": "extractive"}
```

- [ ] **Step 22: Run the tests to verify they pass**

Run: `pytest tests/test_recall_engine.py -v`
Expected: PASS (10 passed)

- [ ] **Step 23: Commit**

```bash
git add smos/memory/recall_engine.py tests/test_recall_engine.py
git commit -m "feat: add chain strategy to recall() — completes strategy dispatch"
```

---

## Task 6: Frequency-based tier promotion

**Files:**
- Modify: `smos/memory/vector_store.py` (add `recompute_tiers_by_frequency`, add `timedelta` to the `datetime` import)
- Modify: `smos/memory/lifecycle.py` (`_promote_tiers`, currently lines 36-47)
- Test: `tests/test_lifecycle_tiers.py` (new)

**Interfaces:**
- Consumes: `VectorStore.count() -> int` (existing), `VectorStore._db`/`VectorStore._lock` (internal, used only inside `VectorStore` itself)
- Produces: `VectorStore.recompute_tiers_by_frequency() -> None`
- `LifecycleManager._promote_tiers()`'s behavior changes from positional slicing to calling `recompute_tiers_by_frequency()`; its signature and callers (`_run_cycle`) are unchanged

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lifecycle_tiers.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smos.memory.lifecycle import LifecycleManager
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _seed(store: VectorStore, n: int) -> list[str]:
    return [
        store.store(MemoryObject(type="doc", content=f"Fact number {i} about a topic."))
        for i in range(n)
    ]


def _set_recall(store: VectorStore, uuid: str, count: int, days_ago: float | None) -> None:
    last_recalled = (
        (datetime.utcnow() - timedelta(days=days_ago)).isoformat() if days_ago is not None else None
    )
    store._db.execute(
        "UPDATE memories SET recall_count = ?, last_recalled = ? WHERE uuid = ?",
        (count, last_recalled, uuid),
    )
    store._db.commit()


def test_promote_tiers_below_min_size_is_noop(store: VectorStore) -> None:
    ids = _seed(store, 3)
    LifecycleManager(store)._promote_tiers()
    for uuid in ids:
        assert store.get_by_uuid(uuid)["metadata"]["tier"] == "hot"


def test_promote_tiers_hot_by_recency(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=0, days_ago=1)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "hot"


def test_promote_tiers_hot_by_recall_count(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=11, days_ago=60)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "hot"


def test_promote_tiers_warm_by_recency(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=0, days_ago=20)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "warm"


def test_promote_tiers_cold_when_never_recalled(store: VectorStore) -> None:
    ids = _seed(store, 12)
    _set_recall(store, ids[0], count=0, days_ago=None)
    LifecycleManager(store)._promote_tiers()
    assert store.get_by_uuid(ids[0])["metadata"]["tier"] == "cold"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_lifecycle_tiers.py -v`
Expected: FAIL — `test_promote_tiers_hot_by_recency`, `test_promote_tiers_hot_by_recall_count`, `test_promote_tiers_warm_by_recency`, and `test_promote_tiers_cold_when_never_recalled` all fail because the current `_promote_tiers()` assigns tiers by insertion-order slicing, not by `recall_count`/`last_recalled`.

- [ ] **Step 3: Implement `recompute_tiers_by_frequency`**

In `smos/memory/vector_store.py`, change the top import line from:

```python
from datetime import datetime, timezone
```

to:

```python
from datetime import datetime, timedelta, timezone
```

Add the method after `bump_recall` (added in Task 1):

```python
    def recompute_tiers_by_frequency(self) -> None:
        now = datetime.now(timezone.utc)
        hot_cutoff = (now - timedelta(days=7)).isoformat()
        warm_cutoff = (now - timedelta(days=30)).isoformat()
        with self._lock:
            self._db.execute(
                """
                UPDATE memories SET tier = CASE
                    WHEN last_recalled >= ? OR recall_count > 10 THEN 'hot'
                    WHEN last_recalled >= ? OR recall_count > 3 THEN 'warm'
                    ELSE 'cold'
                END
                """,
                (hot_cutoff, warm_cutoff),
            )
            self._db.commit()
```

(`last_recalled` is nullable — SQLite evaluates `NULL >= ?` as `NULL`/falsy, so never-recalled memories fall through to the `recall_count`-only checks, which is the intended behavior for `test_promote_tiers_cold_when_never_recalled`.)

- [ ] **Step 4: Replace `_promote_tiers()` in `lifecycle.py`**

In `smos/memory/lifecycle.py`, replace (currently lines 36-47):

```python
    def _promote_tiers(self) -> None:
        uuids = self._store.get_all_uuids()
        n = len(uuids)
        if n < _MIN_STORE_SIZE:
            return
        cold_cut = n // 4
        warm_cut = n // 2
        updates = (
            [(uuid, "cold") for uuid in uuids[:cold_cut]]
            + [(uuid, "warm") for uuid in uuids[cold_cut:warm_cut]]
        )
        self._store.update_tiers_batch(updates)
```

with:

```python
    def _promote_tiers(self) -> None:
        if self._store.count() < _MIN_STORE_SIZE:
            return
        self._store.recompute_tiers_by_frequency()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_lifecycle_tiers.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass (no test asserted the old positional-slicing tier behavior — confirm by scanning failures, if any, before proceeding).

- [ ] **Step 7: Commit**

```bash
git add smos/memory/vector_store.py smos/memory/lifecycle.py tests/test_lifecycle_tiers.py
git commit -m "feat: replace positional tier promotion with frequency/recency thresholds"
```

---

## Task 7: Wire `bump_recall` into existing retrieval tools

**Files:**
- Modify: `smos/tools/semantic_tools.py` (`semantic_query`, `graph_query`)
- Test: `tests/test_semantic_tools.py`
- Test: `tests/test_graph_query_tool.py`

**Interfaces:**
- Consumes: `VectorStore.bump_recall(uuids: list[str])` (Task 1)
- `semantic_query`/`graph_query`'s public signatures and return shapes are unchanged — this task only adds a side effect

- [ ] **Step 1: Write the failing test for `semantic_query`**

Append to `tests/test_semantic_tools.py`:

```python
def test_semantic_query_bumps_recall(mock_store: MagicMock) -> None:
    expected = CompressedContext(summary="s", sources=["id1", "id2"], confidence=0.5)
    with patch("smos.tools.semantic_tools.build_compressed_context", return_value=expected):
        semantic_query("database storage", 5, mock_store)
    mock_store.bump_recall.assert_called_once_with(["id1", "id2"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_semantic_tools.py -v`
Expected: FAIL — `AssertionError: Expected 'bump_recall' to have been called once. Called 0 times.`

- [ ] **Step 3: Wire `bump_recall` into `semantic_query`**

In `smos/tools/semantic_tools.py`, change:

```python
def semantic_query(
    query: str,
    k: int,
    store: VectorStore,
    tags: list[str] | None = None,
) -> dict:
    if not _validate_text(query):
        return _INVALID_INPUT_RESPONSE
    result = build_compressed_context(query, k, store, tags=tags)
    return result.model_dump()
```

to:

```python
def semantic_query(
    query: str,
    k: int,
    store: VectorStore,
    tags: list[str] | None = None,
) -> dict:
    if not _validate_text(query):
        return _INVALID_INPUT_RESPONSE
    result = build_compressed_context(query, k, store, tags=tags)
    store.bump_recall(result.sources)
    return result.model_dump()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_semantic_tools.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add smos/tools/semantic_tools.py tests/test_semantic_tools.py
git commit -m "feat: bump recall_count on tool_semantic_query hits"
```

- [ ] **Step 6: Write the failing test for `graph_query`**

Append to `tests/test_graph_query_tool.py`:

```python
def test_graph_query_bumps_recall_for_memory_nodes(store: VectorStore) -> None:
    m = _mem("The `JWT` token is validated by the middleware.")
    memory_id = store.store(m)
    graph_query("JWT", store, max_hops=1)
    row = store._db.execute(
        "SELECT recall_count FROM memories WHERE uuid = ?", (memory_id,)
    ).fetchone()
    assert row[0] == 1
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `pytest tests/test_graph_query_tool.py -v`
Expected: FAIL — `AssertionError: assert 0 == 1` (recall_count stays 0, nothing bumps it yet)

- [ ] **Step 8: Wire `bump_recall` into `graph_query`**

In `smos/tools/semantic_tools.py`, change:

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

to:

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
    memory_ids = [n["id"] for n in nodes if n["type"] == "memory"]
    store.bump_recall(memory_ids)
    return {"term": term, "start_id": start_id, "nodes": nodes, "count": len(nodes)}
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `pytest tests/test_graph_query_tool.py -v`
Expected: PASS (6 passed)

- [ ] **Step 10: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 11: Commit**

```bash
git add smos/tools/semantic_tools.py tests/test_graph_query_tool.py
git commit -m "feat: bump recall_count on tool_graph_query memory hits"
```

---

## Task 8: Register the 8 new MCP tools in `server.py`

**Files:**
- Modify: `smos/server.py`

**Interfaces:**
- Consumes: `remember`, `forget`, `learn`, `link`, `compress`, `reflect`, `snapshot` (from `smos.tools.agent_primitives`, Tasks 2-4), `recall` (from `smos.memory.recall_engine`, Task 5)
- Produces: `tool_remember`, `tool_forget`, `tool_learn`, `tool_link`, `tool_compress`, `tool_reflect`, `tool_snapshot`, `tool_recall` — 8 new `@app.tool()`-registered MCP functions

No existing `server.py` function is renamed or removed. This task is register-only wiring; there is no dedicated pytest file for `server.py` today (`tests/` has none), so this task's tests are the full pytest suite plus a manual import smoke check.

**Note:** existing `tool_semantic_store`/`tool_semantic_write` (in `server.py`) have a parameter named `type`, which shadows the builtin `type()` inside their own `except Exception as exc: ... type(exc).__name__ ...` blocks — this is a pre-existing latent bug (would raise `TypeError` if either function's `try` block ever threw). It is out of scope to fix here since it doesn't touch this plan's spec, but `tool_remember` below reuses the `type` parameter name for consistency with those tools' calling convention, so it uses `exc.__class__.__name__` instead of `type(exc).__name__` to avoid replicating the same bug in new code.

- [ ] **Step 1: Add the new imports**

In `smos/server.py`, change:

```python
from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore
from smos.tools.file_tools import read_file_compress, write_file_safe
from smos.tools.ingest_tools import (
    do_recursive_semantic_ingest,
    do_bulk_read,
    do_semantic_snapshot_repo,
)
from smos.tools.semantic_tools import (
    _MIN_INPUT_CHARS,
    _INVALID_INPUT_RESPONSE,
    graph_query,
    semantic_query,
    semantic_store,
    semantic_write,
)
```

to:

```python
from smos.memory.lifecycle import LifecycleManager
from smos.memory.recall_engine import recall
from smos.memory.vector_store import VectorStore
from smos.tools.agent_primitives import (
    compress,
    forget,
    learn,
    link,
    reflect,
    remember,
    snapshot,
)
from smos.tools.file_tools import read_file_compress, write_file_safe
from smos.tools.ingest_tools import (
    do_recursive_semantic_ingest,
    do_bulk_read,
    do_semantic_snapshot_repo,
)
from smos.tools.semantic_tools import (
    _MIN_INPUT_CHARS,
    _INVALID_INPUT_RESPONSE,
    graph_query,
    semantic_query,
    semantic_store,
    semantic_write,
)
```

- [ ] **Step 2: Add the 8 tool wrappers**

In `smos/server.py`, add after `tool_graph_query` (after its closing line, currently line 142, before `tool_read_file_compress`):

```python
@app.tool()
def tool_remember(content: str, type: str = "episodic") -> str:
    """Store an episodic memory and return its ID.

    Args:
        content: The text content to remember.
        type: Memory type (default "episodic").
    """
    try:
        if err := _check_input(content, "content"):
            return str(err)
        return remember(content, type, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_forget(memory_id: str) -> dict:
    """Delete a memory and its graph edges.

    Args:
        memory_id: The memory's id to delete.
    """
    try:
        ok = forget(memory_id, _store)
        return {"success": ok, "error": None}
    except Exception as exc:
        return {"success": False, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_learn(content: str) -> str:
    """Store a semantic (high-confidence) memory and return its ID.

    Args:
        content: The text content to learn.
    """
    try:
        if err := _check_input(content, "content"):
            return str(err)
        return learn(content, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_link(id_a: str, id_b: str, edge_type: str) -> dict:
    """Create an explicit graph edge between two memories.

    Args:
        id_a: Source memory id.
        id_b: Target memory id.
        edge_type: One of references, depends_on, related_to, implements,
                   contains, evolves_into, generated_from.
    """
    try:
        return link(id_a, id_b, edge_type, _store)
    except Exception as exc:
        return {"success": False, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}


@app.tool()
def tool_compress(ids: str) -> str:
    """Merge multiple memories into one compressed memory, preserving source links.

    Sources are kept, not deleted — the new memory links back to them via
    source_ids and generated_from graph edges.

    Args:
        ids: Comma-separated memory ids to merge.
    """
    try:
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        return compress(id_list, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_reflect(query: str, k: int = 5) -> str:
    """Reason about what the agent knows relevant to query, store the reflection, return its ID.

    Args:
        query: Natural language question about the agent's own knowledge.
        k: Number of retrieved memories to reflect over (default 5).
    """
    try:
        if err := _check_input(query, "query"):
            return str(err)
        return reflect(query, k, _store)
    except Exception as exc:
        return str({"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"})


@app.tool()
def tool_snapshot() -> dict:
    """Take a repository snapshot of the current project root (no path argument needed)."""
    try:
        return snapshot(str(_PROJECT_ROOT), _store)
    except Exception as exc:
        return {"status": "error", "error": f"{exc.__class__.__name__}: {str(exc)[:300]}"}


@app.tool()
def tool_recall(
    query: str,
    strategy: str = "hybrid",
    k: int = 5,
    since: str = "",
    until: str = "",
    seed_id: str = "",
    component: str = "",
) -> dict:
    """Retrieve memories using an explicit strategy: hybrid, temporal, chain, or repo.

    hybrid (default): the existing vector+BM25+graph retrieval pipeline.
    temporal: filter by [since, until] ISO-8601 timestamps, then rank by similarity.
    chain: follow evolves_into/related_to edges from seed_id; k is hop depth here, not result count.
    repo: hybrid retrieval scoped to a single component tag.

    Args:
        query: Natural language query (unused for the chain strategy).
        strategy: One of hybrid, temporal, chain, repo (default "hybrid").
        k: Result count for hybrid/temporal/repo, or hop depth for chain (default 5).
        since: ISO-8601 lower timestamp bound for temporal (empty = unbounded).
        until: ISO-8601 upper timestamp bound for temporal (empty = unbounded).
        seed_id: Starting memory id for the chain strategy.
        component: Tag to scope the repo strategy to.
    """
    try:
        return recall(query, strategy, k, since, until, seed_id, component, _store)
    except Exception as exc:
        return {"error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}
```

- [ ] **Step 3: Smoke-check the module imports and registers cleanly**

Run: `python -c "import smos.server as s; names = [t for t in dir(s) if t.startswith('tool_')]; print(sorted(names))"`
Expected: prints a list including `tool_remember`, `tool_forget`, `tool_learn`, `tool_link`, `tool_compress`, `tool_reflect`, `tool_snapshot`, `tool_recall` alongside the pre-existing `tool_*` names — no import errors.

- [ ] **Step 4: Run the full suite to check for regressions**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add smos/server.py
git commit -m "feat: register 8 agent-primitive MCP tools in server.py"
```

---

## Task 9: `bench_phase3_primitives.py` benchmark

**Files:**
- Create: `tests/bench_phase3_primitives.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6 (`agent_primitives.py`'s 7 functions, `recall_engine.recall`, `VectorStore`)
- Produces: a standalone script (not pytest-collected) printing latency stats per primitive and per `recall()` strategy, matching the style of `tests/bench_graph_retrieval.py`

- [ ] **Step 1: Write the benchmark script**

Create `tests/bench_phase3_primitives.py`:

```python
"""
bench_phase3_primitives.py — Agent Memory Primitives latency benchmark.

Standalone script (NOT pytest-collected — see tests/test_exact_term_recall.py's
docstring for why files in tests/ named bench_*.py aren't picked up by `pytest
tests/`). Run directly: `python tests/bench_phase3_primitives.py`

Seeds a small corpus, then times each of the 8 agent primitives and each
recall() strategy, reporting per-call latency.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.memory.recall_engine import recall
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.agent_primitives import compress, forget, learn, link, reflect, remember

_SEED_DOCS = [
    "The `auth_service` validates incoming requests before routing them.",
    "`auth_service` delegates token checks to the `middleware` layer.",
    "The `middleware` layer parses the `JWT` from the Authorization header.",
    "PostgreSQL uses MVCC for concurrent transaction isolation.",
    "Redis implements a single-threaded event loop for high throughput.",
]


def _timed(label: str, fn, *args) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn(*args)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"  {label:<20} {elapsed_ms:7.2f}ms")
    return elapsed_ms, result


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="smos_bench_phase3_"))
    try:
        store = VectorStore(persist_path=tmp_dir / "data")
        seed_ids = [store.store(MemoryObject(type="doc", content=d)) for d in _SEED_DOCS]

        print("=== Phase 3 Primitives Benchmark ===\n")
        print("Primitives:")
        _, remember_id = _timed("remember", remember, "Agent noticed a new failure pattern in prod.", "episodic", store)
        _, learn_id = _timed("learn", learn, "The retry backoff must be exponential, not linear.", store)
        _, link_result = _timed("link", link, remember_id, learn_id, "related_to", store)
        _, compress_id = _timed("compress", compress, seed_ids[:2], store)
        _, reflect_id = _timed("reflect", reflect, "what do I know about auth?", 5, store)
        _, forget_result = _timed("forget", forget, remember_id, store)

        print("\nrecall() strategies:")
        _timed("recall(hybrid)", recall, "auth middleware", "hybrid", 5, "", "", "", "", store)
        _timed("recall(repo)", recall, "auth middleware", "repo", 5, "", "", "", "", store)
        _timed("recall(chain)", recall, "", "chain", 2, "", "", seed_ids[0], "", store)
        _timed("recall(temporal)", recall, "auth middleware", "temporal", 5, "", "", "", "", store)

        print(f"\nSeed corpus size: {len(_SEED_DOCS)}")
        print(f"compress() produced: {compress_id}")
        print(f"reflect() produced: {reflect_id}")
        print(f"forget() result: {forget_result}")
        print(f"link() result: {link_result}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `python tests/bench_phase3_primitives.py`
Expected: Prints per-primitive and per-strategy latency in milliseconds, with no exceptions. (LLM-backed calls — `compress`, `reflect`, and `recall(temporal)`'s summarization — require Ollama running locally with the configured model; if it's unavailable, `compress_memories_full`/`reflect_on_memories`'s existing fallback paths keep the script from crashing, per the same convention Phase 1/2 benchmarks rely on.)

- [ ] **Step 3: Commit**

```bash
git add tests/bench_phase3_primitives.py
git commit -m "test: add Phase 3 agent primitives latency benchmark"
```

---

## Final check

- [ ] Run the full suite one more time: `pytest tests/ -v --tb=short` — expect all tests passing (Phase 2 baseline was 142; this plan adds roughly 45 new test cases across Tasks 1-7).
- [ ] Run `python tests/bench_phase3_primitives.py` once more to confirm the benchmark still runs cleanly against the final state of the branch.

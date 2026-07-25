# Phase 5.3 — Ingestion contract, quarantine validation & resumable checkpointing

**Status:** approved design, ready for implementation planning.
**Depends on:** Phase 5.1 (`document` node type, adapter interface, segment metadata columns),
Phase 5.2 (segmentation cascade, `ingest_document_text`, containment tree).
**Parent spec:** `docs/superpowers/specs/2026-07-17-domain-general-ingestion-and-retrieval-design.md`
(§8.6 Pillar G, §11 resolved decisions).

## 1. Goal

Give SMOS a versioned, validated boundary for external document ingestion (Pillar G), so
client-side transformers (Codebeamer, DOORS, Jira, Word/PDF extractors — never built inside
SMOS) can hand SMOS a **structure-preserving** payload instead of flat text, and SMOS can
segment/index/link it centrally, idempotently, and resumably at scale (500-page documents).

This is the natural continuation of 5.1 (foundation types) and 5.2 (the segmentation cascade
and containment tree) — it's the first ingestion path that isn't a local filesystem scan.

## 2. Scope for this sub-phase

**In scope:**
- `smos/ingest/contract.py` — Pydantic ingestion schema + boundary (shape) validation.
- Quarantine storage for payloads that fail shape validation (never silently store an
  unstructured blob).
- Deterministic-id upsert (external-key wins — re-ingesting the same source document is
  idempotent, not a duplicate insert).
- Resumable-checkpoint ingestion for large payloads (a crash partway through doesn't lose
  prior work or leave a half-linked graph).
- `ingest_tools.py` integration (`do_ingest_document_schema`) and a new `tool_ingest_document`
  MCP tool in `server.py`.
- A minimal, real reference transformer (`markdown_to_payload`) proving the contract end to end
  and doubling as the test-fixture builder.

**Explicitly deferred (not this sub-phase):**
- **Pending-edge resolution** for `source_links` whose target isn't ingested yet. No adapter
  extracts real cross-references today (`MarkdownAdapter.extract_references()` is a no-op), so
  building resolution machinery now has nothing real to exercise it. The contract carries a
  `source_links` field (forward-compatible wire format) but SMOS only shape-validates it in
  5.3 — resolution lands in 5.5 (multi-domain traceability & fan-out), once adapters that
  actually populate `extract_references()` exist.
- Quarantine-inspection MCP tool (`tool_ingest_quarantine_list`/`get`) — a plain helper
  function exists for a future session to wire up once something needs it.
- Multi-tenancy/auth/namespacing — deferred entirely per the parent spec's §11 resolution.

## 3. The ingestion contract (`smos/ingest/contract.py`)

Pydantic models, versioned via `schema_version` so the contract can evolve without breaking
existing clients:

```python
class ExternalHandle(BaseModel):
    system: str                          # e.g. "codebeamer", "doors", "jira"
    native_id: str
    deep_link: Optional[str] = None
    version: Optional[str] = None
    source_updated_at: Optional[datetime] = None

class ContentBlock(BaseModel):
    block_type: Literal["heading", "paragraph", "list", "table"]
    text: str                            # verbatim
    heading_level: Optional[int] = None  # required iff block_type == "heading" (1-6)
    source_anchor: Optional[str] = None  # per-block deep link, where the source exposes sub-ids

class SourceLink(BaseModel):
    target_native_id: str
    edge_type: str
    metadata: dict = {}

class IngestionPayload(BaseModel):
    schema_version: str = "1.0"
    system: str
    native_id: str
    doc_name: Optional[str] = None
    external_handle: ExternalHandle
    blocks: list[ContentBlock]
    doc_type: Optional[str] = None       # requirement/standard/stakeholder_need/meeting_note/...
    domain_tags: list[str] = []
    metadata: dict = {}
    source_links: list[SourceLink] = []  # carried, not acted on in 5.3 — see §2
    confidence: float = 1.0
    transformer_name: Optional[str] = None
    transformer_version: Optional[str] = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

`doc_id` is **derived, not client-supplied**: `sha1(f"{system}:{native_id}")[:16]`. This makes
re-ingesting the same source document idempotent by construction — the client never needs to
track or supply an SMOS-side id, only its own `system`/`native_id`.

## 4. Boundary validation & quarantine

`validate_payload(payload: IngestionPayload) -> list[str]` — shape checks, not judgment:
- `blocks` non-empty; every block's `text` non-empty after stripping whitespace.
- every `heading` block has `heading_level` in `1..6`.
- `external_handle.system` and `external_handle.native_id` non-empty.
- every `source_links[i].edge_type` is a member of
  `smos.ingest.adapters.registry.all_edge_types()` (reuses the existing Phase 2/3 registry).
- total payload size (`sum(len(b.text) for b in blocks)`) within a generous sanity cap
  (5,000,000 chars) — rejects pathological payloads before they reach the segmenter.

**On success:** proceed to ingestion (§5).

**On failure:** never store a partial/unstructured blob. Persist the raw payload + errors to a
new `ingest_quarantine` table and return `{"status": "quarantined", "quarantine_key": ...,
"errors": [...]}` synchronously — no exception raised, the client sees exactly what to fix.

```sql
CREATE TABLE ingest_quarantine (
    key            TEXT PRIMARY KEY,
    system         TEXT NOT NULL,
    native_id      TEXT NOT NULL,
    raw_payload    TEXT NOT NULL,   -- JSON
    errors         TEXT NOT NULL,   -- JSON list[str]
    quarantined_at TEXT NOT NULL
)
```

A `get_quarantined(store, key)` helper function exists for future inspection; no MCP tool is
wired to it yet (§2).

## 5. Ingestion pipeline: structural bridging, upsert, checkpointing

### 5.1 Structural bridging (blocks → segmenter)

Concatenate `blocks[].text` into one `full_text`, tracking each block's `(start, end)` char
offsets as it's appended. Build `ContractBlockAdapter(base_adapter)` — a thin `DomainAdapter`
wrapper whose `structural_boundaries()` returns those precomputed offsets directly (no regex
re-detection), while `extract_references`/`llm_boundary_prompt` delegate to `base_adapter`
(a no-op default adapter if the caller doesn't specify a domain adapter). This lets the
*existing, unmodified* segmentation cascade (structural → semantic → size-fit) run unchanged —
the semantic/size-fit layers still handle any individual block that's still oversized after
structural splitting.

### 5.2 Small refactor to `segmenter.py`

Extract the pure tree-building portion of `ingest_document_text` into a new public function so
both the existing (5.2) path and the new checkpointed path can share it without duplication:

```python
def build_segments(text: str, adapter: DomainAdapter) -> list[Span]:
    root = _build_span_tree(text, 0, len(text), adapter)
    _merge_undersized(text, root)
    return _flatten_leaves(root)
```

`ingest_document_text` calls this internally — behavior-preserving; 5.2's existing test suite
keeps passing unchanged.

### 5.3 Deterministic-id upsert semantics

Keyed on the derived `doc_id`:
- **New `doc_id`** → fresh ingest, new checkpoint row.
- **Existing `doc_id`, checkpoint `status='complete'`** → re-sync. Delete the old segments for
  this `doc_id` (new `delete_document_segments(store, doc_id)` helper: look up segment memory
  ids by the `doc_id` column, `store.delete_batch(...)`, drop the old `section` graph nodes)
  and the old checkpoint row, then ingest fresh. The `document` graph node id is stable and is
  not recreated — just repopulated underneath.
- **Existing `doc_id`, checkpoint `status='in_progress'`, same `content_hash`** → resume: skip
  already-stored leaves, continue from `next_leaf_index`.
- **Existing `doc_id`, checkpoint `status='in_progress'`, different `content_hash`** → the
  client sent new content mid-flight; treat as re-sync (clear stale partial segments +
  checkpoint, start over).

`content_hash = sha1(full_text)` — cheap, deterministic, used only to distinguish "resume" from
"re-sync with new content," not for dedup (dedup stays scoped-out per the 5.1 resolution).

### 5.4 Resumable checkpointing

```sql
CREATE TABLE ingest_checkpoints (
    doc_id          TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,
    total_leaves    INTEGER NOT NULL,
    next_leaf_index INTEGER NOT NULL DEFAULT 0,
    segment_ids     TEXT NOT NULL DEFAULT '',  -- comma-joined, accumulated in order
    status          TEXT NOT NULL DEFAULT 'in_progress',
    updated_at      TEXT NOT NULL
)
```

`build_segments()` is pure/cheap (no I/O), so it's simply recomputed from `full_text` on every
call including resume; only the **storing** side is checkpointed. Leaves are stored via
`store.store_batch()` in chunks (default 64, aligned with `store_batch`'s own batching); after
each chunk commits, `next_leaf_index` and `segment_ids` are updated. Once every leaf has a
`memory_id` (some from this run, some restored from a prior checkpoint), `_wire_containment`
runs exactly once and the checkpoint flips to `status='complete'`.

**Consequence:** a mid-document crash leaves already-stored segments searchable but **not yet
wired** into the containment tree — never a half-linked graph (the spec's "atomic or resumable"
requirement, resolved as resumable). A resume finishes the job without re-embedding or
re-storing anything already done.

## 6. `ingest_tools.py` / `server.py` integration

```python
# smos/tools/ingest_tools.py
def do_ingest_document_schema(
    payload: IngestionPayload,
    store: VectorStore,
    adapter: Optional[DomainAdapter] = None,
) -> dict:
    errors = validate_payload(payload)
    if errors:
        key = quarantine(store, payload, errors)
        return {"status": "quarantined", "quarantine_key": key, "errors": errors}
    doc_id = derive_doc_id(payload.external_handle.system, payload.external_handle.native_id)
    # upsert branch (§5.3) + checkpointed ingest (§5.4)
    return {"status": "success", "doc_id": doc_id, "segment_count": ..., "resumed": bool, ...}
```

```python
# smos/server.py
@app.tool()
def tool_ingest_document(payload_json: str) -> dict:
    """Ingest a structure-preserving document via the versioned ingestion contract.

    payload_json: JSON string matching smos.ingest.contract.IngestionPayload.
    Non-conforming payloads are quarantined (not rejected) with validation errors returned.
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

A malformed-JSON/wrong-type `ValidationError` from Pydantic itself is a harder failure than a
quarantine-worthy *content* violation, so it short-circuits before quarantine logic; quarantine
is specifically for well-formed-but-content-invalid payloads.

## 7. Reference transformer (`smos/ingest/transformers/markdown_transformer.py`)

A minimal, real (not test-only) transformer proving the client-side half of the contract:

```python
def markdown_to_payload(
    text: str, system: str, native_id: str, doc_name: Optional[str] = None
) -> IngestionPayload:
    """Parse ATX headings + paragraphs into typed ContentBlocks and wrap in a valid IngestionPayload."""
```

Splits on the same ATX-heading grammar `MarkdownAdapter` already uses for structural detection,
but instead of returning `(start, end)` spans over flat text, it emits typed `ContentBlock`s
(`heading` blocks with `heading_level`, `paragraph` blocks for everything else — no list/table
parsing in this minimal version, YAGNI). This documents the contract by example and is reused
as 5.3's own test-fixture builder rather than duplicating a second ad hoc payload constructor
in tests.

## 8. Testing plan

- `test_contract.py` — schema validation (valid payload passes; each boundary check
  individually fails correctly); quarantine round-trip (invalid payload → row persisted,
  correct errors returned, no exception raised).
- `test_contract_ingest.py` — upsert semantics (fresh doc; re-sync replaces segments; resume
  continues from checkpoint without re-storing/re-embedding already-checkpointed leaves —
  verified via a forced mid-ingest failure/resume in a real test, not a mock); byte-exact
  reconstruction still holds for contract-ingested docs (reuses 5.2's reconstruction-test
  pattern); containment tree is only wired after full completion (assert no `contains` edges
  exist for a doc stuck mid-checkpoint).
- `test_markdown_transformer.py` — round-trip a sample markdown doc through the transformer →
  payload → `do_ingest_document_schema` → reconstruct, matching 5.2's end-to-end style.
- `tool_ingest_document` — thin integration test in the existing server-tool test style.

## 9. Schema/component impact summary

| File | Action |
|---|---|
| `smos/ingest/contract.py` (new) | `IngestionPayload`/`ExternalHandle`/`ContentBlock`/`SourceLink`, `validate_payload`, `derive_doc_id`, quarantine read/write helpers |
| `smos/ingest/segmenter.py` | Extract `build_segments()` (pure refactor, behavior-preserving) |
| `smos/ingest/transformers/markdown_transformer.py` (new) | `markdown_to_payload()` reference transformer |
| `smos/tools/ingest_tools.py` | `do_ingest_document_schema()`, `delete_document_segments()`, checkpointed store/wire loop |
| `smos/memory/vector_store.py` | New `ingest_checkpoints` / `ingest_quarantine` tables (same migration pattern as existing `ALTER TABLE`/`CREATE TABLE IF NOT EXISTS` calls) |
| `smos/server.py` | New `tool_ingest_document` MCP tool |
| `tests/test_contract.py`, `test_contract_ingest.py`, `test_markdown_transformer.py` (new) | Coverage per §8 |

## 10. Out of scope / carried forward

- Pending-edge resolution → 5.5.
- Quarantine-inspection MCP tool → whenever an operator workflow actually needs it.
- Multi-tenancy/auth/namespacing → deferred indefinitely per parent spec.
- List/table block parsing in the reference transformer → extend when a real domain needs it
  (YAGNI for 5.3; headings + paragraphs already exercise the full pipeline).

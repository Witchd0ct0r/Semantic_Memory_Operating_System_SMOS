# Scoped Retrieval, Discovery and Stored Blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release 0.5.0: tag scoping that really filters (pre-filter), document/tag listing with totals and paging, numeric scores, and stored parser blocks so every hit maps exactly into the document text.

**Architecture:** A `memory_tags` table (store migration 4, kept in sync by the single insert helper and a delete trigger) yields an eligible row set before ranking; the FAISS flat index is searched through an id selector and BM25 through an allow-set. A `document_blocks` table (migration 5) stores the parser's raw block ranges; hits are enriched from it. A no-op re-send keeps segment ids.

**Tech Stack:** Python 3.x, FastAPI, SQLite (WAL), FAISS 1.14 (`IndexIDMap(IndexFlatIP)`), numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-scoped-retrieval-and-discovery-design.md` (read it first; parts A–D below map to it).

## Global Constraints

- Develop on `main`; run tests with `python -m pytest tests/ -q` (fallback `rtk proxy python -m pytest tests/ -q`). Use `python`, never `python3`.
- New tables are new numbered **store** migrations in `smos/storage/migrations.py` (`STORE_MIGRATIONS`, currently versions 1–3): `4 memory_tags`, `5 document_blocks`. Never edit migrations 1–3.
- Every `/api/v1` route except `/health` depends on `require_token`; `tests/test_api_scopes.py` pins the read/write operation lists. New routes are **read** routes (`@read.get`).
- Tags match exactly and case-sensitively; `tags`, `tags_all`, `tags_none` combine with AND; `tags` is any-of.
- `k` stays `1..100`; `offset + k > 1000` is an HTTP 400 (never clamped).
- Offsets are Unicode code points (Python `str` indices) into the text `/nodes/{id}/text` returns.
- No new dependencies and no new file types under `smos/` (updater constraints, `docs/RELEASING.md`).
- No company data in the repo (no host names, no benchmark figures).
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- A tag containing SQL/LIKE characters (`norm:MBST%`, `a_b`) must match only itself (task 2 test).
- An empty tag list, or a blank-string tag, means "no filter", never "match nothing" (task 4 test).
- Re-ingesting a document with changed tags must leave no stale `memory_tags` rows (task 2 test).
- Page 2 (`offset=k`) must not repeat or skip page 1, and `offset` past the end returns an empty list with a correct `total` (task 4 test).
- Hits from documents with no stored blocks, and non-segment memories (no `doc_id`), must return null block fields, not errors (task 8 test).
- Loading a pre-0.5.0 backup migrates it and scoping works on it at once (task 10 test).

---

## Part A — Tag scoping (commit group A; ships as its own commits)

### Task 1: Migration 4 — `memory_tags`

**Files:**
- Modify: `smos/storage/migrations.py` (add `_memory_tags`, append `(4, "memory_tags", _memory_tags)` to `STORE_MIGRATIONS`)
- Test: `tests/test_memory_tags.py`

**Interfaces:**
- Produces: table `memory_tags(row_id INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(tag, row_id)) WITHOUT ROWID` + index `idx_memory_tags_row ON memory_tags(row_id)`; trigger `memory_tags_delete AFTER DELETE ON memories` removing `memory_tags` rows of `OLD.row_id`.

- [ ] **Step 1: Write failing tests** in `tests/test_memory_tags.py`: `test_migration_4_backfills_existing_rows` (build a v3 database with `run_migrations(conn, STORE_MIGRATIONS[:3])`, insert `memories` rows with `tags='a:b,c'` and `''`, run the full list, assert `memory_tags` holds exactly `(row_id,'a:b'),(row_id,'c')` and nothing for the empty one); `test_delete_trigger_removes_tags` (delete a memories row, assert its tag rows are gone).
- [ ] **Step 2: Run** `python -m pytest tests/test_memory_tags.py -q` → FAIL (no such table).
- [ ] **Step 3: Implement** `_memory_tags(conn)`: create table, index, trigger; backfill with a Python loop over `SELECT row_id, tags FROM memories WHERE tags != ''` using `executemany` in chunks of 5000 (tags are comma-separated, tags never contain `,`; skip blank pieces).
- [ ] **Step 4: Run** → PASS; also `python -m pytest tests/test_backup_load.py tests/test_graph_store.py -q` still passes.
- [ ] **Step 5: Commit** `feat: store migration 4, memory_tags with backfill`.

### Task 2: Single insert helper, `TagFilter`, eligibility queries

**Files:**
- Create: `smos/memory/tagfilter.py`
- Modify: `smos/memory/vector_store.py` (`store` ~140, `store_batch` ~519; new methods)
- Test: `tests/test_memory_tags.py` (extend)

**Interfaces:**
- Produces (`smos/memory/tagfilter.py`):
  - `@dataclass(frozen=True) class TagFilter: any: tuple[str, ...] = (); all: tuple[str, ...] = (); none: tuple[str, ...] = ()` with `is_empty` property (true when all three are empty after dropping blank strings) and classmethod `make(any=None, all=None, none=None) -> TagFilter` that strips blanks.
  - `@dataclass(frozen=True) class Eligible: row_ids: np.ndarray` (int64) and `uuids: frozenset[str]`.
- Produces (`VectorStore`):
  - `_insert_memory_row(self, memory: MemoryObject) -> int` (lock held; inserts the `memories` row **and** its `memory_tags` rows; returns `row_id`); `store` and `store_batch` use it instead of their inline INSERTs.
  - `resolve_tag_filter(self, f: TagFilter) -> Optional[Eligible]` (`None` when `f.is_empty`).
  - `count_total(self) -> int` is not needed: use existing `count()`.
  - `unknown_tags(self, tags: list[str]) -> list[str]` (names with zero `memory_tags` rows, input order, de-duplicated).
  - `tag_counts(self, prefix: str = "", limit: int = 1000, offset: int = 0) -> tuple[int, list[dict]]` returning `(total_distinct_tags, [{"tag", "count"}...])` sorted by tag; `prefix` matches with `substr`/`>=`/`<` range, **not** LIKE.

- [ ] **Step 1: Write failing tests**: `test_memory_tags_in_sync_after_store_batch_and_delete` (store, `delete`, `delete_batch`; assert `memory_tags` equals the split of `memories.tags` for all rows after each); `test_reingest_with_changed_tags_leaves_no_stale_rows` (ingest a document with `domain_tags=["a"]`, re-send with `["b"]` and different text; assert no `a` rows for that doc's former rows and `unknown_tags(["a"]) == ["a"]`); `test_wildcard_characters_match_only_themselves` (tags `norm:MBST%` and `norm:MBSTX`; `resolve_tag_filter(TagFilter.make(any=["norm:MBST%"]))` returns only the first); `test_resolve_semantics` (any / all / none combine with AND; case-sensitive: `Norm:X` ≠ `norm:X`); `test_tag_counts_prefix_and_paging`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Resolve with set algebra in SQL (`INTERSECT`/`EXCEPT` over `SELECT row_id FROM memory_tags WHERE tag IN (...)`, one INTERSECT term per `all` tag, join `memories` for `uuid`); chunk the `IN` lists below 500 parameters. Re-ingest cleanup relies on `delete_batch` (trigger).
- [ ] **Step 4: Run** `python -m pytest tests/test_memory_tags.py tests/test_contract_ingest.py tests/test_vector_store_query_metadata.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: single memory insert helper, tag filter resolution`.

### Task 3: Pre-filtered vector and keyword search

**Files:**
- Modify: `smos/memory/vector_store.py` (`query_domain` ~192, `_query_internal` ~200), `smos/memory/bm25_index.py` (`query` ~211)
- Test: `tests/test_tag_scoping.py` (create), `tests/test_bm25_equivalence.py` must stay green

**Interfaces:**
- Produces:
  - `VectorStore.query_domain(self, query_text: str, k: int = 20, domain_tags: Optional[list[str]] = None, eligible: Optional[Eligible] = None) -> list[dict]`: when `eligible` is given, search the index through `faiss.SearchParameters` with `sel=faiss.IDSelectorBatch(eligible.row_ids)` so the top-`k` is taken from eligible rows only (verified to work on `IndexIDMap` in faiss 1.14.3); `domain_tags` (kept for existing callers/tests) is converted to `Eligible` via `resolve_tag_filter(TagFilter.make(any=domain_tags))`. Result ordering ties break by `row_id` ascending.
  - `VectorStore.cosine_scores(self, query_text: str, uuids: list[str]) -> dict[str, float]`: exact cosine of the query against the stored vectors (`self._index.reconstruct(row_id)` via a row_id lookup), for keyword-only hits.
  - `BM25Index.query(self, text: str, k: int, allowed: Optional[frozenset[str]] = None) -> list[tuple[str, float]]`: with `allowed`, slots of other uuids score as dead (excluded, including from the zero-score padding).

- [ ] **Step 1: Write failing tests** in `tests/test_tag_scoping.py` using a real `VectorStore` fixture (`VectorStore(persist_path=tmp_path/"data")`): `test_narrow_tag_still_returns_k_hits` (60 memories tagged `big`, 3 tagged `rare`, query text similar to the big ones; `query_domain(..., k=3, domain_tags=["rare"])` returns all 3 rare, which the old post-filter would not); `test_bm25_allowed_excludes_others` (and with `allowed=None` unchanged); `test_cosine_scores_match_vector_search` (cosine for a vector hit equals its `score` within 1e-5).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** as specified above.
- [ ] **Step 4: Run** `python -m pytest tests/test_tag_scoping.py tests/test_bm25_equivalence.py tests/test_bm25_index.py tests/test_contract_ingest.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: pre-filtered vector and BM25 search`.

### Task 4: `build_context` — scoping, totals, paging, determinism

**Files:**
- Modify: `smos/compression/context_builder.py` (`build_context` ~102), `smos/memory/schemas.py` (`RetrievedContext` ~34), `smos/memory/recall_engine.py`, `smos/tools/semantic_tools.py` (`semantic_query` ~28)
- Modify tests that mock the store: `tests/test_hybrid_retrieval.py`, `tests/test_graph_expansion.py` (set `store.resolve_tag_filter.return_value = None`, `store.count.return_value = len(...)`, `store.cosine_scores.return_value = {}`, `store.unknown_tags.return_value = []`)
- Test: `tests/test_tag_scoping.py` (extend)

**Interfaces:**
- Produces:
  - `RetrievedContext` gains `total: Optional[int] = None`, `unknown_tags: list[str] = []`, `offset: int = 0`.
  - `build_context(query: str, k: int, store: VectorStore, tags: Optional[list[str]] = None, *, tags_all: Optional[list[str]] = None, tags_none: Optional[list[str]] = None, offset: int = 0) -> RetrievedContext`.
  - `semantic_query(query, k, store, tags=None, *, tags_all=None, tags_none=None, offset=0) -> dict`.
  - `recall(query, strategy, k, since, until, seed_id, component, store, *, tags=None, tags_all=None, tags_none=None, offset=0) -> dict`; `repo` merges `component` into `tags`; `hybrid` now honours the tag arguments; other strategies ignore them.
- Behaviour: `eligible = store.resolve_tag_filter(TagFilter.make(...))`; `candidate_k = max((offset + k) * _CANDIDATE_MULTIPLIER, 20)`; vector and BM25 legs both restricted to `eligible`; **the unfiltered fallback is removed**; RRF merge keeps `(offset + k) * 2`; sort key is `(-rrf, -term_overlap, id)`; the page is `candidates[offset:offset + k]`; graph-neighbour extras are added only when `offset == 0` and only if the neighbour's uuid is in `eligible.uuids` (when filtering); `total = len(eligible.row_ids)` if filtering else `store.count()`; `unknown_tags = store.unknown_tags(tags + tags_all)`; every returned item gets a numeric `score` (missing ones filled from `store.cosine_scores`).

- [ ] **Step 1: Write failing tests** (real store): `test_scoped_query_never_returns_out_of_scope` (the SMOS-001 regression: three tag groups, query phrased to match all, `tags=["norm:A"]`, k=20 → every result's `metadata.tags` contains `norm:A`, including keyword-only and graph-neighbour hits); `test_no_fallback_for_empty_scope` (`tags=["nobody"]` → `results == []`, `unknown_tags == ["nobody"]`, `total == 0`); `test_tags_all_and_none_combine` ; `test_total_le_k_means_complete` (6 eligible, k=10 → 6 results, `total == 6`; k=5 → 5 results, `total == 6`); `test_paging_is_stable` (offset 0/5/10 pages concatenated equal one k=15 call; no duplicates); `test_offset_past_end_is_empty_with_total`; `test_blank_and_empty_tags_mean_no_filter`; `test_scores_always_numeric` (a keyword-only hit has a float score).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the behaviour block. Keep `_term_overlap`/`_rrf_merge` helpers; add the id tie-break.
- [ ] **Step 4: Run** `python -m pytest tests/test_tag_scoping.py tests/test_hybrid_retrieval.py tests/test_graph_expansion.py tests/test_reference_and_domain_tagging_e2e.py tests/test_api_routes.py -q` → PASS; then full suite `python -m pytest tests/ -q`.
- [ ] **Step 5: Commit** `fix: tag scoping is a real pre-filter (no leaks, no fallback); totals, paging, numeric scores`.

### Task 5: API surface for scoping and paging

**Files:**
- Modify: `smos/api/models.py` (`QueryIn` ~24, `RecallIn` ~30), `smos/api/routes.py` (`post_query` ~103, `post_recall` ~118; fix the `/query` and `/memories` descriptions)
- Test: `tests/test_api_routes.py` (extend) or new `tests/test_api_scoping.py`

**Interfaces:**
- Produces: `QueryIn` gains `tags_all: list[str] = []`, `tags_none: list[str] = []`, `offset: int = Field(default=0, ge=0)`; `RecallIn` gains `tags`, `tags_all`, `tags_none`, `offset`. A helper `_check_page(k: int, offset: int) -> None` in `routes.py` raises `HTTPException(400, "offset + k must not exceed 1000")`. The `tags` field description reads "Restrict to items having any of these tags. Combined with tags_all and tags_none by AND."

- [ ] **Step 1: Write failing tests** with the `api` fixture: `test_query_tags_restrict_over_http` (POST two tagged memories under different tags, query with one tag → only that one, `total` present); `test_offset_cap_is_400` (`offset=990,k=20` → 400 with message; `offset=980,k=20` → 200); `test_unknown_tags_reported`; `test_recall_component_uses_same_filter`; `test_read_token_can_query_with_tags` (read-scope token → 200).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `python -m pytest tests/test_api_routes.py tests/test_api_scopes.py tests/test_api_openapi.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: tags_all/tags_none/offset on query and recall`.

## Part B — Discovery (commit group B)

### Task 6: `GET /documents` and `GET /tags`

**Files:**
- Modify: `smos/memory/vector_store.py` (new `list_documents`), `smos/api/routes.py` (two `@read.get` routes), `tests/test_api_scopes.py` (add `("GET", "/api/v1/documents")` and `("GET", "/api/v1/tags")` to `READ_OPERATIONS`)
- Test: `tests/test_discovery_api.py` (create)

**Interfaces:**
- Produces: `VectorStore.list_documents(self, system: str = "", tags: tuple[str, ...] = (), limit: int = 100, offset: int = 0) -> tuple[int, list[dict]]`: document graph nodes **excluding placeholders** (`metadata.placeholder` true), sorted by `id`, `tags` all-of over `domain_tags + ["doctype:<doc_type>"]`; each row `{"id", "external_handle": {"system","native_id"}, "doc_type", "tags", "segment_count", "updated_at", "document_hash"}` (the last three from `ingest_checkpoints`; `null` if absent). Routes: `GET /documents?system=&tag=&tag=&limit=100&offset=0` (`limit` ≤ 500) → `{"total", "documents"}`; `GET /tags?prefix=&limit=1000&offset=0` → `{"total", "tags": [{"tag","count"}]}`.

- [ ] **Step 1: Write failing tests**: `test_documents_listing_filters_and_pages` (3 docs in 2 systems; filter by system, by two `tag` params = all-of; pages by `limit=1` concatenate to the full sorted-by-id list); `test_placeholder_documents_not_listed` (a payload with a `source_link` to a never-ingested target); `test_tags_listing_counts_and_prefix`; `test_read_token_can_list` (read-scope token → 200 on both); `test_limit_cap_422_or_400` (`limit=501` rejected).
- [ ] **Step 2–4:** run → FAIL, implement, run `python -m pytest tests/test_discovery_api.py tests/test_api_scopes.py tests/test_api_auth.py -q` → PASS (`test_every_route_in_schema_is_protected` must stay green).
- [ ] **Step 5: Commit** `feat: GET /documents and GET /tags`.

## Part C — Stored blocks and hit enrichment (commit group C)

### Task 7: Migration 5 and ingest writes `document_blocks`

**Files:**
- Modify: `smos/storage/migrations.py` (`_document_blocks`, `(5, "document_blocks", ...)`), `smos/memory/vector_store.py`, `smos/tools/ingest_tools.py` (`delete_document_segments` ~33, `_ingest_locked` ~62)
- Test: `tests/test_document_blocks.py` (create)

**Interfaces:**
- Produces: table `document_blocks(doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL, char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, block_type TEXT NOT NULL, PRIMARY KEY(doc_id, ordinal))` + index `(doc_id, char_start)`. `VectorStore.save_blocks(self, doc_id: str, blocks: list[tuple[int, int, str]]) -> None` (replaces all rows of `doc_id` in one transaction; tuples are `(char_start, char_end, block_type)`, ordinal = list index); `delete_blocks(self, doc_id: str) -> None`; `get_blocks(self, doc_id: str) -> list[dict]` (`{"ordinal","char_start","char_end","block_type"}` ordered); `block_containing(self, doc_id: str, pos: int) -> Optional[dict]` (block with `char_start <= pos < char_end`).
- Ingest: spans come from `blocks_to_full_text` (`block_spans`) and `payload.blocks[i].block_type`, **not** from the segmenter; written before segments are created; `delete_document_segments` also calls `delete_blocks`.

- [ ] **Step 1: Write failing tests**: `test_migration_5_creates_table_on_existing_store`; `test_ingest_stores_blocks_that_tile_the_text` (fixture payload of 5 blocks incl. one under 20 chars; assert ordinals `0..n-1`, first start 0, each start == previous end, last end == `len(full_text)`, and `"".join(text[s:e])` == `full_text`); `test_short_block_range_survives_segment_merge`; `test_reingest_replaces_blocks`.
- [ ] **Step 2–4:** run → FAIL, implement, run `python -m pytest tests/test_document_blocks.py tests/test_contract_ingest.py tests/test_ingest_concurrency.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: store migration 5, parser blocks kept as document_blocks`.

### Task 8: Enrich hits; extend `/nodes/{id}/text`

**Files:**
- Modify: `smos/compression/context_builder.py` (`to_retrieved_context` ~60), `smos/memory/exact_retrieval.py` (`reconstruct` ~78), `smos/api/routes.py` (`get_node_text` ~162, add `include: str = ""` query param), `smos/memory/vector_store.py` (`get_document_hashes`)
- Test: `tests/test_hit_enrichment.py` (create)

**Interfaces:**
- Produces: `VectorStore.get_document_hashes(self, doc_ids: list[str]) -> dict[str, str]` (from `ingest_checkpoints.content_hash`). Every hit's `metadata` gains `document_hash`, `block_ordinal`, `block_ordinal_end`, `block_char_start`, `block_char_end`, `block_type`: `block_*` from `block_containing(doc_id, char_start)` and `block_containing(doc_id, char_end - 1)` (`block_char_*` are the range of the first block, `block_ordinal_end` of the second); **all `null`** when the hit has no `doc_id`, or the document has no stored blocks. `reconstruct(store, node_id, include_blocks=False)` adds `document_hash`, `char_length` (document nodes: end of the last segment; sections: `null`) and, with `include=blocks`, `blocks: [...]` from `get_blocks`; `complete` for a document node additionally requires first `char_start == 0`.

- [ ] **Step 1: Write failing tests**: `test_segment_hit_carries_block_range_and_hash`; `test_hit_without_blocks_has_null_fields` (a document ingested, then `DELETE FROM document_blocks`; and a plain `/memories` hit); `test_merged_short_block_hit_reports_end_ordinal`; `test_text_endpoint_returns_hash_length_and_blocks` (`include=blocks`; without it no `blocks` key); `test_complete_false_when_first_segment_does_not_start_at_zero` (delete the first segment of a stored document).
- [ ] **Step 2–4:** run → FAIL, implement, run `python -m pytest tests/test_hit_enrichment.py tests/test_exact_retrieval.py tests/test_api_routes.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: block range and document hash on every hit; text endpoint returns blocks and hash`.

### Task 9: Alignment acceptance test on a real ingest

**Files:**
- Test: `tests/test_block_alignment.py` (create); no production code unless it finds a defect

- [ ] **Step 1: Write the test** through the HTTP API (`api` fixture, POST `/documents`, then `/query` and `GET /nodes/{doc_id}/text?include=blocks`) with a fixture document containing: two clauses with the **same heading number**; German text with `ä ö ü ß` before the clause being checked; a clause split into `"<head>"` and `"<head> (cont.)"` blocks; blocks ending in `\n`; a block of fewer than 20 characters; one block over 800 characters. Assertions: (a) `text == "".join(block.text for block in payload.blocks)`; (b) blocks tile `text` (property in spec Part C); (c) for every segment hit, `text[char_start:char_end] == hit.content`; (d) for every hit, `block_char_start <= char_start` and `char_end <= block_char_end` of the end block; (e) the two same-numbered headings resolve to **different** `block_ordinal`s; (f) `complete is True`; (g) `char_length == len(text)`.
- [ ] **Step 2: Run** `python -m pytest tests/test_block_alignment.py -q`. Expected PASS. **If (b), (c) or (f) fail because the segmenter leaves a gap, stop and report to the Governor with the failing fixture; do not paper over it.**
- [ ] **Step 3: Commit** `test: alignment of segments, blocks and document text`.

## Part D — Re-send fast path and release (commit group D)

### Task 10: No-op re-send keeps segments; old-store migration check

**Files:**
- Modify: `smos/tools/ingest_tools.py` (`_ingest_locked`, right after the checkpoint is read, before `delete_document_segments`)
- Test: `tests/test_resend_fast_path.py` (create); extend `tests/test_backup_load.py`

**Interfaces:**
- Behaviour: fast path applies iff `checkpoint["status"] == "complete"`, `checkpoint["content_hash"] == text_hash`, and the existing document node's `metadata["domain_tags"]`/`["doc_type"]` equal the payload's. Then: `save_blocks`, `upsert_node` (refresh metadata), `wire_source_links`, document summary as today (`set_summary` or `delete_summary`), and return `{"status": "success", "doc_id", "segment_count", "segment_ids" (from `get_segment_ids_by_doc` in ordinal order), "section_count": None, "reference_count": None, "link_count", "resumed": False, "segments_kept": True}`. Any difference takes the existing replace path, whose result gains `"segments_kept": False`.

- [ ] **Step 1: Write failing tests**: `test_identical_resend_keeps_segment_ids_and_writes_blocks` (ingest, delete `document_blocks` to simulate a 0.4.0 document, re-send identical payload → segment ids unchanged, blocks present, `segments_kept` true); `test_changed_text_replaces_segments`; `test_changed_tags_replaces_segments_and_memory_tags`; `test_resend_without_summary_deletes_document_summary` (pins the documented behaviour); `test_segment_summary_survives_fast_path`. In `tests/test_backup_load.py`: `test_loading_pre_0_5_backup_migrates_and_scopes` (build a store at schema version 3 with tagged memories, load it through the backup loader, assert versions 4 and 5 applied, `resolve_tag_filter` works).
- [ ] **Step 2–4:** run → FAIL, implement, run `python -m pytest tests/test_resend_fast_path.py tests/test_backup_load.py tests/test_contract_ingest.py tests/test_ingest_summary.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: identical re-send keeps segments and backfills blocks`.

### Task 11: Docs, version, release record

**Files:**
- Modify: `smos/__init__.py` (`__version__ = "0.5.0"`), `docs/API.md`, `docs/guide/` (parser/agent pages: scoping, `total`, paging, blocks, `document_hash`, re-send behaviour and the summary gotcha), `docs/INSTALL.md` only if it mentions the schema, `docs/runbooks/` (new `scoped-retrieval.md`: migration time check on a store copy, how to re-send documents to backfill blocks), `docs/HANDOFF.md`
- Create: `docs/scoped-retrieval-verification.md` (test counts, a real-server run of query scoping, paging, tiling check; no company data)
- Test: `tests/test_docs_guide.py`, `tests/test_versioning.py`

- [ ] **Step 1:** Update docs and version; release notes text (kept in the verification record) name behaviour change A (`tags` never leaks; unknown tag → empty result with `unknown_tags`), the migrations, and that old documents have null block fields until re-sent.
- [ ] **Step 2:** `python -m pytest tests/ -q` → all pass (expect ≈1145 + new tests). Real-server check: start the server on a temp data dir, ingest the alignment fixture, run scoped queries, page through, compare `/text` tiling; record results; stop the server.
- [ ] **Step 3: Commit** `docs: 0.5.0 guide, runbook and verification record; version 0.5.0`. Do not tag or publish; the Governor releases per `docs/RELEASING.md`.

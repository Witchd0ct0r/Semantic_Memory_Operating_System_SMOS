# SMOS Architecture Analysis

> This document was produced by SMOS analyzing its own codebase. Claude read 10 source
> files (982 lines total) using `tool_read_file_compress`, stored compressed summaries in
> the semantic index, then ran 4 targeted `tool_semantic_query` calls to synthesise the
> analysis below — without re-reading a single file at synthesis time.
>
> **Context window at synthesis: ~850 tokens (4 queries × ~210 tokens each).
> Without SMOS: ~4,910 tokens of raw source held in context.**

---

## 1. End-to-End Data Flow

### 1.1 Write Path — `tool_semantic_store` / `tool_semantic_write`

```
MCP client → stdio → FastMCP (server.py)
  │
  ├─ _check_input() — min 10 chars
  │
  ├─ semantic_store() / semantic_write() [tools/semantic_tools.py]
  │    └─ MemoryObject(type, content, timestamp=utcnow(), tags=[])
  │
  └─ VectorStore.store(memory) [memory/vector_store.py]
       acquire _lock
       │
       ├─ embed(memory.content) → 384-dim float32  (CPU-bound, holds _lock)
       ├─ sqlite3 INSERT INTO memories → commit() → row_id = lastrowid
       ├─ faiss.IndexIDMap.add_with_ids(vector, [row_id])
       ├─ if count % save_interval == 0: faiss.write_index()
       release _lock
       │
       append memory.id to _new_ids (under _new_ids_lock)
       │
       └─ if count % 50 == 0: LifecycleManager.on_insert()
            └─ daemon thread: _promote_tiers() → _deduplicate()

Return: memory.id (UUID)
```

### 1.2 Read Path — `tool_semantic_query`

> **Updated for Phase 1 (BM25 hybrid retrieval, merged 2026-07-02, commit `b890a19`).**
> `_classify_domain` and the fixed 70/30 rerank are gone — see below.

```
MCP client → FastMCP
  └─ semantic_query() → build_compressed_context() [compression/context_builder.py]
       │
       ├─ store.query_domain(query, k=max(k×4, 20), domain_tags) [falls back to
       │    store.query() if tags omitted or filter returns no results]
       │    └─ VectorStore._query_internal()
       │         acquire _lock
       │         ├─ embed(query) → vector  (CPU-bound, holds _lock)
       │         ├─ faiss.search(vector, n) → scores[], row_ids[]
       │         └─ for each id: SELECT ... WHERE row_id=?  (N round-trips)
       │         release _lock
       │
       ├─ store.bm25.query(query, k=candidate_k) → (uuid, score) pairs
       │    zero-score entries (no term match) filtered out before fusion
       │
       ├─ _rrf_merge(vector_results, bm25_results, top_k=k×2)
       │    └─ score = 1/(60+rank_vector) + 1/(60+rank_bm25), summed per uuid
       │
       ├─ hydrate merged uuids: prefer vector hit (carries FAISS distance);
       │    BM25-only hits fetched via store.get_by_uuid(), distance defaulted
       │
       ├─ final sort: (rrf_score, term_overlap) — RRF is the primary key,
       │    term overlap only breaks ties between equal/near-equal RRF scores
       │
       └─ compress_memories_full(top_k, query) [llm/summarizer.py]
            └─ Ollama API call (synchronous) → retry once → extractive fallback

Return: CompressedContext {summary, sources, confidence, mode}
```

### 1.3 File Read Path — `tool_read_file_compress`

```
MCP client → FastMCP
  └─ read_file_compress(path, store) [tools/file_tools.py]
       ├─ Path resolution — null-byte check + traversal guard
       ├─ file.read_text()
       ├─ summarize_text(content) [llm/summarizer.py]  ← LLM call
       └─ VectorStore.store(MemoryObject(type="doc", tags=["file", path]))

Return: {summary, id, source, error}
```

### 1.4 Structured Document Path — `tool_ingest_document` (Phase 5)

> Added after Phase 5.5 and the requirements work (2026-10-13). Source of truth for the
> payload fields is [docs/API.md §9](./docs/API.md#9-structured-document-ingestion-tool_ingest_document).

Documents that must be stored **exactly** and **linked** bypass the lossy summary path:

```
MCP client → tool_ingest_document(payload_json)
  ├─ IngestionPayload.model_validate_json     (type/schema error → {"status":"error"}, not quarantined)
  └─ do_ingest_document_schema(payload, store, adapter=None)   [tools/ingest_tools.py]
       ├─ validate_payload()  — errors → quarantine_payload() → {"status":"quarantined", ...}
       ├─ doc_id = sha1("system:native_id")[:16]          (idempotent upsert key)
       ├─ blocks_to_full_text() → full_text + block spans;  content_hash
       ├─ checkpoint: complete or changed content → delete_document_segments() (re-sync);
       │              in-progress with same hash → resume
       ├─ build_span_tree(): structural (the payload's own block spans) → semantic → size-fit
       ├─ graph.upsert_node(doc_id, "document", name, metadata{domain_tags, doc_type, system,
       │                                                      native_id, metadata})
       ├─ store_batch(segments) in chunks of 64, tags = domain_tags + "doctype:<doc_type>",
       │    save_checkpoint() after every chunk
       ├─ _wire_containment(): document --contains--> section --contains--> segment
       ├─ _wire_references(): adapter.extract_references() → segment --edge--> concept:{name}
       │    (targets the adapter marks document-local → concept:{doc_id}:{name})
       ├─ wire_source_links(): document --edge_type--> document   [ingest/link_wiring.py]
       │    owned edges (origin="source_link") replaced; unknown targets → placeholder document node
       └─ save_checkpoint(status="complete")

Return: {status:"success", doc_id, segment_count, segment_ids, section_count,
         reference_count, link_count, resumed}
```

Notes:

- Over MCP no domain adapter is passed (a no-op adapter is used), so `reference_count` is 0 and
  relationships come from `source_links`. In-text citation/term extraction (`MarkdownAdapter`,
  `LegalAdapter`) is available through the Python entry point.
- `GraphStore.upsert_node` (not `add_node`, which is `INSERT OR IGNORE`) refreshes the document
  node on re-sync and upgrades placeholders; a re-sync therefore also updates `doc_name`.
- Containment (`contains`) is reserved for document structure. Item-to-item hierarchy uses
  `parent_of`, and `reconstruct()` / re-sync only walk `contains`.
- `LegalAdapter` provision citations are scoped per document
  (`concept:{doc_id}:{name}`), so two regulations' "Article 3" no longer collide; defined terms
  stay global so they can join across documents.

### 1.5 Requirement Context Path — `tool_requirement_context`

```
MCP client → tool_requirement_context(text|doc_id|system+native_id, level, domain_tags,
                                      normative_tags, declared_links_json, token_budget, k)
  └─ requirement_context_from_strings() → requirement_context(store, ...)   [memory/requirement_context.py]
       ├─ resolve: stored item (document node, reconstruct() text, tags) and/or draft text
       ├─ declared links → hop-1 seeds (`declared <edge>`), unknown targets → unresolved_links
       ├─ linked:  BFS over depends_on / parent_of / member_of, both directions, ≤ 2 hops,
       │           placeholders omitted (reported), cycles cut by a visited set
       ├─ candidates: store.query(text) ⊕ bm25.query(text) → RRF, pool = max(k·100, 1000)
       ├─ peers:   one best segment per document; same level:/domain tag; not normative,
       │           not already linked; near_duplicate at cosine ≥ 0.90
       ├─ normative: explicit `cites` targets first, then doctype:legal|standard segments,
       │           optional normative_tags scope
       ├─ coverage: has_upstream, has_downstream, near_duplicates, normative_by_source,
       │            unresolved_links
       └─ per-section budget = token_budget // 3; first item clipped rather than dropped

Return: {query, linked, peers, normative, coverage, truncated}   — no LLM call anywhere
```

---

## 2. Lock Contention Map

| Lock | Guards | Held by |
|---|---|---|
| `VectorStore._lock` | FAISS index + SQLite connection + `_insert_count` | All store/query/delete/tier operations |
| `VectorStore._new_ids_lock` | `_new_ids: list[str]` | `store()` append; `_drain_new_ids()` clear |
| `LifecycleManager._running` | "cycle running" flag (semaphore) | `_run_cycle()` — non-blocking acquire |
| `file_tools._LOG_LOCK` | `logs/writes.jsonl` file handle | `_log_write()` |

### Key contention scenario

`store()` and `_query_internal()` both call `embed()` while holding `_lock`. `SentenceTransformer.encode()` is CPU-bound (10–200ms on CPU). While embedding runs, **all** concurrent query, store, and delete calls block. The server is effectively single-threaded for vector operations.

**No deadlock risk** — `_lock` and `_new_ids_lock` are never held simultaneously.

---

## 3. Failure Mode Catalogue

### Ollama unreachable
Query path: `compress_memories_full` catches the connection error and falls back to extractive summary (first sentence from top-3 memories). Mode returns `"uncertain"`. No crash, degraded quality.

File compress path: `summarize_text` falls back to first 3 sentences of file. Lower quality but stored successfully.

### FAISS index corrupted
`_load_or_create_index()` catches the read error and returns a fresh empty index. `_rebuild_index_if_stale()` detects the divergence (FAISS count = 0, SQLite count = n) and re-embeds all rows from SQLite in batches of 256. SQLite is the authoritative store — full recovery on next startup.

### SQLite locked (external writer)
WAL mode allows concurrent readers but an external write lock will block INSERT calls. If INSERT raises after FAISS `add_with_ids` already ran, the FAISS entry exists without a SQLite row — **divergence**. Resolved on next startup by `_rebuild_index_if_stale()` (SQLite wins, orphaned FAISS entry is dropped).

### Lifecycle cycle failure
All exceptions in `_run_cycle()` are swallowed (`except Exception: pass`). The `_running` lock is released in `finally`. Next cycle fires at the next 50th insert.

---

## 4. Scaling Characteristics

### Per-operation complexity

| Operation | Complexity | Notes |
|---|---|---|
| `embed(text)` | O(L) | CPU-bound; scales with sequence length |
| `IndexFlatIP.search(query, k)` | **O(n·d)** | Brute-force — primary scaling bottleneck |
| `IndexIDMap.remove_ids` | O(n) | Full index scan to reconstruct |
| SQLite INSERT | O(log n) | B-tree on uuid |
| SQLite SELECT by row_id | O(log n) | Indexed primary key |
| `_promote_tiers()` | O(n) | Every 50 inserts |
| `_deduplicate()` | O(M·n) | M new inserts × O(n) FAISS search each |
| `delete()` | O(n) + disk write | Triggers full index save |

### Scaling ceiling by corpus size

| Corpus | Bottleneck | Notes |
|---|---|---|
| 0–5K | None significant | All operations fast |
| 5K–50K | FAISS O(n·d) search | 100–1000ms query latency on CPU |
| 50K+ | FAISS + O(M·n) dedup cascades | Interactive latency breaks down; IVF/HNSW needed |

**The benchmarks (14ms P95 at 100K) reflect SIMD-accelerated hardware. On CPU-only hardware the ceiling is closer to 10K–20K before query latency becomes noticeable.**

---

## 5. Known Design Issues

These were identified during self-analysis and are tracked for future releases.

### High priority

1. **`embed()` called while holding `_lock`**  
   Moving `embed()` outside the lock (compute embedding, then acquire lock to insert) would allow concurrent embedding and significantly improve throughput under load.

2. **N SQLite round-trips per query result**  
   `_query_internal()` issues one `SELECT` per result row. Replace with `WHERE row_id IN (...)` to batch all lookups into one query.

3. **No input size guard before LLM calls**  
   `compress_memories_full` passes all candidate content to the LLM without checking total token count. Large candidate sets silently exceed the model's context window.

### Medium priority

4. ~~**Domain classification is effectively dead code**~~ — **Resolved in Phase 1**
   (2026-07-02). `_classify_domain` and the fixed 70/30 rerank were removed; replaced
   with BM25 hybrid retrieval via Reciprocal Rank Fusion (see §1.2). `tags` filtering
   on `query_domain` is still opt-in/caller-supplied, not automatic classification.

5. **`delete()` saves the full FAISS index on every single deletion**  
   Should batch saves or defer to the periodic `save_interval` logic.

6. **`lru_cache` on `_get_model()` is not thread-safe during first population**  
   If two threads call `embed()` simultaneously before the model is cached, both may attempt model load concurrently.

7. **NEW (Phase 1) — `BM25Index.update()`/`.remove()` full-rebuild cost**
   The single-item paths rebuild the entire `BM25Okapi` index per call — O(n) per
   call, O(n²) cumulative at scale, held inside `VectorStore._lock`. `add_batch()`/
   `remove_batch()` (used by `store_batch()`/`delete_batch()`) amortize this to once
   per call, but single `store()`/`delete()` calls still pay the full rebuild.
   Accepted as a documented Phase-1 limitation; needs an incremental-index redesign
   before Phase 4 (HNSW) if corpora grow large.

### Added with Phase 5 / requirements work

11. **NEW (Phase 5 / requirements) — bounded candidate pool for `peers`/`normative`**
    Both sections are filtered by tag out of one shared hybrid candidate pool
    (`max(k·100, 1000)`). A corpus with far more requirement segments than the pool can
    push every relevant legal/standard segment out, and `coverage.normative_by_source` then
    reports a gap that does not exist. A tag-indexed lookup (rank only normative segments)
    would remove the bound.

12. **NEW — near-duplicate threshold is a default** (cosine ≥ 0.90), validated only on
    synthetic fixtures. Calibrate against real requirement data before trusting it.

### Low priority

8. **Double input validation** — `server.py::_check_input()` and `semantic_tools.py::_validate_text()` both enforce the 10-char minimum. One is redundant.

9. **Tier promotion is insertion-order-based, not recency-based** — the oldest 25% of entries (by row_id) are always "cold" regardless of how recent they are.

10. **`compress_memories` backward-compat wrapper discards `mode`** — callers cannot distinguish abstractive from extractive results.

---

## 6. Strengths

- **Crash recovery** — SQLite is the authoritative store; FAISS is always rebuildable.
- **Graceful LLM degradation** — both summarisation paths have extractive fallbacks; Ollama failures never crash callers.
- **Batch operations** — `delete_batch`, `update_tiers_batch` reduce lock acquisitions.
- **Lifecycle guard** — `_running` non-blocking acquire prevents cycle pile-up under insert pressure.
- **Path traversal protection** — null-byte check + `resolve()` + `relative_to()` in `file_tools.py`.
- **WAL mode** — allows SQLite readers during writes; reduces metadata-read lock pressure.
- **BM25 hybrid retrieval (Phase 1)** — Reciprocal Rank Fusion over FAISS vector search
  and BM25Okapi fixes exact-term queries (function names, error codes, identifiers)
  that pure vector search missed. Exact-term recall gate: 1.00 hybrid vs 0.33
  vector-only on a tuned corpus.

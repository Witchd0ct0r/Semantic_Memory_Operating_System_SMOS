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

```
MCP client → FastMCP
  └─ semantic_query() → build_compressed_context() [compression/context_builder.py]
       │
       ├─ _classify_domain(query) — keyword counting over 5 built-in domains
       │
       ├─ store.query_domain(query, candidate_k=max(k×4, 20), domain_tags)
       │    └─ VectorStore._query_internal()
       │         acquire _lock
       │         ├─ embed(query) → vector  (CPU-bound, holds _lock)
       │         ├─ faiss.search(vector, n) → scores[], row_ids[]
       │         └─ for each id: SELECT ... WHERE row_id=?  (N round-trips)
       │         release _lock
       │         [fallback to full search if domain filter returns < k results]
       │
       ├─ _rerank(query, candidates, top_k=k)
       │    └─ 0.7 × FAISS cosine score + 0.3 × query term overlap
       │
       └─ compress_memories_full(reranked, query) [llm/summarizer.py]
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

4. **Domain classification is effectively dead code**  
   `_classify_domain` filters by built-in keyword lists, then uses those keywords as tag filters. But `semantic_store` stores memories with no tags — tag filtering returns 0 results for virtually all queries, causing immediate fallback to full search. Domain classification adds a second FAISS query with no benefit.  
   *Fix: expose `tags` on `tool_semantic_store` so callers can tag at store time.*

5. **`delete()` saves the full FAISS index on every single deletion**  
   Should batch saves or defer to the periodic `save_interval` logic.

6. **`lru_cache` on `_get_model()` is not thread-safe during first population**  
   If two threads call `embed()` simultaneously before the model is cached, both may attempt model load concurrently.

### Low priority

7. **Double input validation** — `server.py::_check_input()` and `semantic_tools.py::_validate_text()` both enforce the 10-char minimum. One is redundant.

8. **Tier promotion is insertion-order-based, not recency-based** — the oldest 25% of entries (by row_id) are always "cold" regardless of how recent they are.

9. **`compress_memories` backward-compat wrapper discards `mode`** — callers cannot distinguish abstractive from extractive results.

---

## 6. Strengths

- **Crash recovery** — SQLite is the authoritative store; FAISS is always rebuildable.
- **Graceful LLM degradation** — both summarisation paths have extractive fallbacks; Ollama failures never crash callers.
- **Batch operations** — `delete_batch`, `update_tiers_batch` reduce lock acquisitions.
- **Lifecycle guard** — `_running` non-blocking acquire prevents cycle pile-up under insert pressure.
- **Path traversal protection** — null-byte check + `resolve()` + `relative_to()` in `file_tools.py`.
- **WAL mode** — allows SQLite readers during writes; reduces metadata-read lock pressure.
- **Hybrid reranking** — 70% FAISS cosine + 30% term overlap improves precision for keyword-heavy queries without full BM25.

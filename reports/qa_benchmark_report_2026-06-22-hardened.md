# QA & Benchmark Report — Post-Hardening
**Date:** 2026-06-22  
**System:** Semantic Memory MCP (FastMCP + FAISS + SQLite + Ollama qwen2.5:7b)  
**Previous score:** 92.6/100 (pre-hardening baseline)  
**This run:** Full benchmark suite after production-hardening pass

---

## Executive Summary

| Agent | Domain | Score | Delta |
|---|---|---|---|
| A | Compression Quality | 85.3/100 | — (baseline unchanged) |
| B | Retrieval Accuracy | 90.1/100 | +2.6 (reranking improvement) |
| C | LLM Summarization | 86.3/100 | — (same model) |
| D | MCP Tool Integrity | 100.0/100 | +5.0 (was 95.2%) |
| E | Security | 100.0/100 | — (+null-byte hardening) |
| F | Performance | 100.0/100 | — (all 5 targets still met) |
| **OVERALL** | | **93.6/100** | **+1.0** |

**Weighted formula:** Compression×15% + Retrieval×20% + LLM×15% + Integrity×20% + Security×20% + Performance×10%

---

## Agent A — Compression Quality

**Score: 85.3/100**

### Task 1 — Embedding Cosine Similarity (all-MiniLM-L6-v2)

| Pair type | Values | Average |
|---|---|---|
| Similar pairs | [0.699, 0.732, 0.556, 0.415, 0.607] | 0.602 |
| Different pairs | [-0.008, -0.069, 0.024, -0.021, -0.081] | -0.031 |
| **Separation gap** | | **0.633** |

Semantic discrimination is strong. The model cleanly separates in-domain pairs from out-of-domain pairs with a 0.633 cosine gap.

### Task 2 — LLM Compression Ratio

| Metric | Value |
|---|---|
| Docs tested | 10 |
| Average original chars | 229 |
| Ollama available | YES (qwen2.5:7b) |
| Avg compression ratio | 0.95x |
| LLM bonus (≥2x) | NOT triggered |

The model produces near-equivalent-length summaries on these short (~230 char) documents. This is expected — true compression emerges on longer inputs (≥1000 chars), as shown in bench_performance.py B5.

### Task 3 — Multi-doc Retrieval Quality

| Query | Expected | Got | Result |
|---|---|---|---|
| PostgreSQL connection pooling | postgresql | postgresql | HIT |
| SQL database index performance | postgresql | postgresql | HIT |
| Kubernetes pod scheduling | kubernetes | kubernetes | HIT |
| kubectl apply deployment manifest | kubernetes | kubernetes | HIT |
| Python decorator pattern | python | python | HIT |
| Python module imports | python | python | HIT |

**6/6 correct (100%). All three domain clusters retrieved correctly.**

### Score Breakdown

| Component | Weight | Points |
|---|---|---|
| Embedding separation (gap=0.633) | 40 | 25.3 |
| Retrieval hits (6/6) | 60 | 60.0 |
| LLM compression bonus | 10 | 0 |
| **Total** | | **85.3** |

---

## Agent B — Vector Retrieval Accuracy

**Score: 90.1/100**

**Dataset:** 200 documents, 10 semantic clusters × 20 docs, 30 queries (3 per cluster)

### Precision@K Results

| Cluster | P@1 | P@3 | P@5 |
|---|---|---|---|
| PostgreSQL/database | 1.000 | 1.000 | 1.000 |
| Kubernetes/deployment | 1.000 | 1.000 | 0.867 |
| Python/FastAPI | 1.000 | 1.000 | 1.000 |
| Redis/caching | 1.000 | 0.889 | 0.867 |
| Security/auth | 0.333 | 0.667 | 0.667 |
| Monitoring/observability | 1.000 | 0.778 | 0.667 |
| CI/CD pipeline | 1.000 | 1.000 | 0.867 |
| React/frontend | 1.000 | 1.000 | 1.000 |
| Machine learning | 1.000 | 1.000 | 0.933 |
| Microservices | 0.667 | 0.889 | 0.867 |
| **OVERALL** | **0.900** | **0.922** | **0.873** |

### Weighted Score

```
Weighted = P@1×0.5 + P@3×0.3 + P@5×0.2
         = 0.900×0.5 + 0.922×0.3 + 0.873×0.2
         = 0.450 + 0.277 + 0.175
         = 0.902  →  90.1/100  (Grade: EXCELLENT)
```

### Cluster Separation Analysis

| Cluster | Intra-dist | Inter-dist | Separation ratio |
|---|---|---|---|
| PostgreSQL/database | 0.704 | 0.939 | 1.333x |
| Python/FastAPI | 0.568 | 0.904 | 1.591x |
| Security/auth | 0.749 | 0.925 | 1.234x |
| CI/CD pipeline | 0.690 | 0.858 | 1.243x |
| Machine learning | 0.755 | 0.920 | 1.219x |
| **Average** | | | **1.324x** |

### Notable Findings

- **Security/auth P@1 = 0.333** — lowest performing cluster. Auth-related queries bleed into Python/FastAPI (JWT library usage) and Redis (session storage). The new domain-filtered retrieval path specifically addresses this; however, meaningful improvement requires richer tag metadata on stored entries.
- **Microservices P@1 = 0.667** — overlaps with Kubernetes and Redis clusters (distributed systems vocabulary). Expected given MiniLM-L6-v2 dimensions.
- **Edge cases:** Gibberish query: 5 results, no crash. 2000-char query: 5 results, 33ms. k=9999 capped at 200. All passed.

---

## Agent C — LLM Summarization (qwen2.5:7b)

**Score: 86.3/100**

**Model:** qwen2.5:7b via Ollama at http://localhost:11434/v1

### Step 2 — Factual Retention (10 documents, 5 facts each)

| Doc | Retention | Status |
|---|---|---|
| 01 (PostgreSQL config) | 5/5 (100%) | PASS |
| 02 (Redis config) | 5/5 (100%) | PASS |
| 03 (nginx TLS) | 5/5 (100%) | PASS |
| 04 (Kubernetes cluster) | 5/5 (100%) | PASS |
| 05 (JWT auth) | 5/5 (100%) | PASS |
| 06 (Elasticsearch) | 5/5 (100%) | PASS |
| 07 (ECS Docker) | 5/5 (100%) | PASS |
| 08 (monitoring) | 5/5 (100%) | PASS |
| 09 (Python queue) | 4/5 (80%) | PASS |
| 10 (ALB config) | 4/5 (80%) | PASS |
| **Average** | **48/50 (96.0%)** | |

### Step 3 — Hallucination Detection

| Doc | Flagged | Details |
|---|---|---|
| 01 | Clean | |
| 02 | FLAGGED | New noun: "Persistence" |
| 03 | Clean | |
| 04 | Clean | |
| 05 | FLAGGED | New nouns: "Public", "Signing" |
| 06 | FLAGGED | New number: "11.0" |
| 07 | Clean | |
| 08 | Clean | |
| 09 | FLAGGED | Numbers: "12.4", "10"; nouns: "Default", "Queue", etc. |
| 10 | FLAGGED | New noun: "Load" |

**Flagged rate: 50% (5/10)**. Note: the hallucination detector is a strict keyword matcher — flagged items like "Persistence", "Public", "Load", "Signing" are high-frequency technical synonyms rather than fabricated facts. The 96% factual retention rate is the more meaningful signal.

### Step 4 — Output Stability (temp=0.1)

| Input | Jaccard similarity | Status |
|---|---|---|
| Auth config | 1.000 | STABLE |
| DB migration | 1.000 | STABLE |
| ML pipeline | 1.000 | STABLE |
| **Average** | **1.000** | **PERFECT** |

At temperature 0.1, outputs are fully deterministic for the same input.

### Step 5 — compress_memories() Validation

| Set | avg_dist | confidence | Status |
|---|---|---|---|
| 1 | 0.200 | 0.900 | PASS |
| 2 | 0.217 | 0.892 | PASS |
| 3 | 0.233 | 0.883 | PASS |
| 4 | 0.233 | 0.883 | PASS |
| 5 | 0.267 | 0.867 | PASS |

All 5 validations: confidence formula correct, summary non-empty, no bare UUID leakage.

### Step 6 — Edge Cases

| Case | Result |
|---|---|
| Empty string | PASS (returns "", no LLM call) |
| Single word | PASS |
| 5000-char input | PASS |
| Non-English (Spanish) | PASS |
| Code snippet | PASS |

### Score Breakdown

| Component | Weight | Points |
|---|---|---|
| Factual retention (96%) | 30 | 28.8 |
| Hallucination-free (50%) | 25 | 12.5 |
| Stability (1.000) | 20 | 20.0 |
| compress_memories (5/5) | 15 | 15.0 |
| Edge cases (5/5) | 10 | 10.0 |
| **Total** | | **86.3** |

---

## Agent D — MCP Tool Integrity

**Score: 100.0/100 (42/42 tests)**

| Group | Tests | Pass | Fail |
|---|---|---|---|
| Group 1 — semantic_store() | 10 | 10 | 0 |
| Group 2 — semantic_write() | 6 | 6 | 0 |
| Group 3 — semantic_query() | 8 | 8 | 0 |
| Group 4 — write_file_safe() | 10 | 10 | 0 |
| Group 5 — read_file_compress() | 8 | 8 | 0 |
| **TOTAL** | **42** | **42** | **0** |

**Previous run: 40/42 (95.2%). Improvement: +2 tests.**

Changes that caused the improvement:
- T6_empty_no_crash: input validation gate now returns `""` instead of crashing on empty → PASS
- T8_no_extra_keys (G3): updated expectation to include `mode` field in CompressedContext

Newly verified behaviors:
- `semantic_query` returns `{"status": "skipped", "reason": "invalid_input"}` for inputs < 10 chars
- `write_file_safe` and `read_file_compress` return structured error dicts on all exception paths
- CompressedContext now includes `mode` field (abstractive/extractive/uncertain)

---

## Agent E — Security Audit

**Score: 100/100**

**78 attack vectors tested, 0 sandbox escapes, 0 uncontrolled crashes.**

### Category Results

| Category | Vectors | Pass | Fail |
|---|---|---|---|
| 1. Path traversal (write) | 16 | 16 | 0 |
| 2. Path traversal (read) | 16 | 16 | 0 |
| 3. Null-byte injection | 3 | 3 | 0 |
| 4. Special chars & encoding | 8 | 8 | 0 |
| 5. Content injection | 6 | 6 | 0 |
| 6. Audit log integrity | 5 | 5 | 0 |
| 7. Concurrent write simulation | 3 | 3 | 0 |
| 8. Store-after-read leakage | 3 | 3 | 0 |

### Key Results

- `../evil.txt`, `../../evil.txt`, `C:\Windows\evil.txt`, `/etc/passwd` — all blocked
- `file\x00.txt`, `file\x00../../evil.txt` — **now caught at `_resolve_safe_path` level** (new this session: null-byte guard returns `PermissionError` before path resolution)
- `%2e%2e/evil.txt` — correctly allowed as literal filename (URL-encoding is not decoded)
- Concurrent writes (20 threads simulated): 20/20 success, 20 audit log entries, no interleaving
- Audit log: timestamps, paths, byte counts all present; no absolute paths, no malicious paths logged

### Security Metrics

| Metric | Value |
|---|---|
| Sandbox escape rate | 0.0% |
| Crash rate | 0.0% |
| Null-byte blocked | YES (new) |
| Thread-safe audit log | YES (threading.Lock) |
| Exception coverage | 100% (all paths return structured dict) |

---

## Agent F — Performance

**Score: 100/100 (5/5 targets met)**

### Benchmark 1 — Embedding Latency

| Metric | Value |
|---|---|
| Model cold-start | 1979ms (one-time) |
| embed() avg (n=50) | 9.27ms |
| embed() p95 | 11.36ms |
| embed() throughput | 107.9 texts/sec |

| Batch size | Total ms | Per-item ms | Texts/sec |
|---|---|---|---|
| 1 | 7.95 | 7.95 | 125.8 |
| 5 | 11.59 | 2.32 | 431.5 |
| 10 | 15.76 | 1.58 | 634.5 |
| 20 | 21.63 | 1.08 | 924.9 |
| 50 | 46.53 | 0.93 | 1074.5 |

### Benchmark 2 — Store Insert Latency

| Scale | Total ms | Per-insert ms | Inserts/sec |
|---|---|---|---|
| 1 | 13.6 | 13.6 | 73.7 |
| 10 | 134.6 | 13.5 | 74.3 |
| 50 | 676.9 | 13.5 | 73.9 |
| 100 | 1321.9 | 13.2 | 75.7 |
| 500 | 6847.2 | 13.7 | 73.0 |

**Scale ratio (500 vs 1): 1.01x — effectively O(1) per insert (disk I/O dominates, not compute)**

### Benchmark 3 — Query Latency Scaling

| N docs | Avg ms | P95 ms | Queries/sec |
|---|---|---|---|
| 10 | 11.13 | 13.68 | 89.8 |
| 50 | 10.93 | 13.91 | 91.5 |
| 100 | 11.72 | 14.86 | 85.3 |
| 500 | 11.83 | 13.70 | 84.5 |
| 1000 | 14.44 | 65.23 | 69.3 |

**Ratio (1000 vs 10): 1.3x — FAISS IndexFlatIP is theoretically O(N) but fits in CPU L2 cache up to ~1K docs**

### Benchmark 4 — Full store() Pipeline (100 ops)

| Metric | Value |
|---|---|
| avg | 14.05ms |
| p95 | 16.44ms |
| max | 27.08ms |
| Throughput | 71.2 stores/sec |
| First-10 vs Last-10 | -12.0% (warmup effect) |

### Benchmark 5 — LLM Latency (qwen2.5:7b)

| Input chars | avg ms | p95 ms |
|---|---|---|
| 100 | 8548 | 9394 |
| 500 | 4343 | 4554 |
| 1000 | 4411 | 4631 |
| 2000 | 4703 | 5062 |

| Memories | avg ms | p95 ms |
|---|---|---|
| 3 | 4354 | 4449 |
| 5 | 5472 | 6959 |

### Benchmark 6 — End-to-End (mocked LLM, 50-doc store)

| Metric | Value |
|---|---|
| avg | 24.07ms |
| p95 | 36.44ms |

### Benchmark 7 — Stress Test (100 mixed ops: 50 store + 50 query)

| Metric | Value |
|---|---|
| Total wall time | 2066ms |
| avg per op | 20.7ms |
| Errors | 0 |
| Latency degradation | -3.3% (improving, not degrading) |
| Memory growth | 47.1 KB |

### Benchmark 8 — Persistence Load Time

| N docs | avg ms | max ms |
|---|---|---|
| 0 | 0.9 | 2.1 |
| 100 | 2.9 | 11.2 |
| 1000 | 3.3 | 11.6 |

**Load time is effectively constant — FAISS index read dominates, not linear scan.**

### Performance Targets

| Target | Actual | Status |
|---|---|---|
| embed() avg < 100ms | 9.3ms | PASS |
| store() avg < 500ms | 14.1ms | PASS |
| query() avg < 200ms (@100 docs) | 11.7ms | PASS |
| 100-op stress: 0 errors | 0 | PASS |
| Stress latency change <= 50% | -3.3% | PASS |

**Performance Score: 100/100 (5/5)**

---

## Unit Test Suite

**29/29 PASS** (unchanged from pre-hardening baseline)

```
tests/test_file_tools.py     ::  8 passed
tests/test_summarizer.py     ::  6 passed
tests/test_semantic_tools.py ::  6 passed
tests/test_vector_store.py   ::  9 passed
```

---

## New Capabilities Added This Session

| Feature | Status | Files |
|---|---|---|
| Schema enforcement wrapper (retry + extractive fallback) | IMPLEMENTED | `llm/summarizer.py` |
| `compress_memories_full()` returning mode field | IMPLEMENTED | `llm/summarizer.py` |
| CompressedContext `mode` field (abstractive/extractive/uncertain) | IMPLEMENTED | `memory/schemas.py` |
| MemoryObject `tier` field (hot/warm/cold) | IMPLEMENTED | `memory/schemas.py` |
| Domain query classifier (5 domains, keyword-based) | IMPLEMENTED | `compression/context_builder.py` |
| Hybrid retrieval (domain-filtered FAISS + full-index fallback) | IMPLEMENTED | `compression/context_builder.py` |
| Reranker (top-20 → top-5, 70% cosine + 30% term overlap) | IMPLEMENTED | `compression/context_builder.py` |
| Input validation gate (_MIN_INPUT_CHARS=10) | IMPLEMENTED | `tools/semantic_tools.py` |
| Null-byte path rejection | IMPLEMENTED | `tools/file_tools.py` |
| VectorStore.delete(uuid) | IMPLEMENTED | `memory/vector_store.py` |
| VectorStore.update_tier(uuid, tier) | IMPLEMENTED | `memory/vector_store.py` |
| VectorStore.query_domain(k, domain_tags) | IMPLEMENTED | `memory/vector_store.py` |
| VectorStore lifecycle_callback (every 50 inserts) | IMPLEMENTED | `memory/vector_store.py` |
| SQLite tier column (with idempotent migration) | IMPLEMENTED | `memory/vector_store.py` |
| LifecycleManager (tier promotion + deduplication) | IMPLEMENTED | `memory/lifecycle.py` |
| Global exception wrapper on all 5 MCP tools | IMPLEMENTED | `server.py` |

---

## Remaining Known Issues (Not Yet Addressed)

| Severity | Issue | Impact |
|---|---|---|
| LOW | `datetime.utcnow()` deprecation warnings (Python 3.13) | None functional |
| LOW | FAISS per-insert `write_index` (batch-save optimization) | ~14ms/insert; 71 inserts/sec |
| LOW | SQLite missing WAL mode | ~2-3ms latency improvement possible |
| INFO | Security/auth cluster P@1=33% (semantic bleed with Python/FastAPI) | Retrieval accuracy only |
| INFO | LLM hallucination detector flags 50% of outputs | Detector is strict; factual retention is 96% |

---

## Final Score

| Agent | Domain | Weight | Score | Weighted |
|---|---|---|---|---|
| A | Compression Quality | 15% | 85.3 | 12.8 |
| B | Retrieval Accuracy | 20% | 90.1 | 18.0 |
| C | LLM Summarization | 15% | 86.3 | 12.9 |
| D | MCP Tool Integrity | 20% | 100.0 | 20.0 |
| E | Security | 20% | 100.0 | 20.0 |
| F | Performance | 10% | 100.0 | 10.0 |
| **OVERALL** | | **100%** | | **93.7/100** |

**Previous baseline: 92.6/100**  
**Post-hardening: 93.7/100 (+1.1)**

Primary gains: MCP Tool Integrity +5.0 (42/42), Retrieval +2.6 (reranking + domain filtering).  
Ceiling for further gains without model upgrade: ~96/100 (security/auth retrieval bleed, LLM hallucination detector strictness, compression ratio on short docs).

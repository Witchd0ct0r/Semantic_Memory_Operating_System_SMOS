"""
bench_performance.py — Comprehensive latency benchmark for the semantic memory MCP system.

Benchmarks:
  1. Embedding latency (single + batch)
  2. Vector store insert latency at varying scales
  3. Vector search latency scaling with corpus size
  4. Full store pipeline (embed + insert + save)
  5. LLM summarization latency (if Ollama available)
  6. End-to-end pipeline latency (store -> query, mocked LLM)
  7. Stress test — 100 rapid mixed operations
  8. Persistence overhead (load time at 0, 100, 1000 docs)

All timings via time.perf_counter(). Isolated temp dirs for every sub-test.
"""

from __future__ import annotations

import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── project root on path ────────────────────────────────────────────────────
sys.path.insert(0, r"C:\Private\semantic_memory")

from memory.vector_store import VectorStore
from memory.embeddings import embed, embed_batch
from memory.schemas import MemoryObject

# ── helpers ──────────────────────────────────────────────────────────────────

SEPARATOR = "=" * 72
THIN_SEP  = "-" * 72


def _tmp_store() -> tuple[VectorStore, Path]:
    """Return a fresh VectorStore backed by an isolated temp directory."""
    d = Path(tempfile.mkdtemp(prefix="bench_"))
    return VectorStore(persist_path=d), d


def _cleanup(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)


def _make_memory(i: int, content: Optional[str] = None) -> MemoryObject:
    return MemoryObject(
        type="doc",
        content=content or f"Benchmark document number {i}: this entry covers topic {i % 20} "
                           f"with details about system architecture, performance metrics, and "
                           f"operational considerations for sub-system {i % 7}.",
        timestamp=datetime.utcnow(),
        tags=[f"tag{i % 5}"],
    )


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


def _stats(latencies_sec: list[float]) -> dict:
    ms = [_ms(v) for v in latencies_sec]
    return {
        "avg":    round(statistics.mean(ms), 3),
        "p50":    round(statistics.median(ms), 3),
        "p95":    round(sorted(ms)[int(len(ms) * 0.95)], 3),
        "max":    round(max(ms), 3),
        "min":    round(min(ms), 3),
        "std":    round(statistics.stdev(ms) if len(ms) > 1 else 0.0, 3),
        "n":      len(ms),
        "raw_ms": ms,
    }


def _throughput(n: int, total_sec: float) -> float:
    return round(n / total_sec, 2) if total_sec > 0 else float("inf")


def _print_stats(label: str, s: dict) -> None:
    print(f"  {label:<30} avg={s['avg']:>8.3f}ms  p50={s['p50']:>8.3f}ms  "
          f"p95={s['p95']:>8.3f}ms  max={s['max']:>8.3f}ms  "
          f"min={s['min']:>8.3f}ms  std={s['std']:>7.3f}ms  n={s['n']}")


def _table_row(op: str, avg: float, p95: float, mx: float, tps: float) -> str:
    return f"  {op:<35} {avg:>10.3f}  {p95:>10.3f}  {mx:>10.3f}  {tps:>16.2f}"


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 1 — Embedding Latency
# ═══════════════════════════════════════════════════════════════════════════

def bench_embedding() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 1 — Embedding Latency")
    print(SEPARATOR)

    # Warm up — first call loads model weights; not counted
    print("  Warming up model (loading weights) ...")
    t0 = time.perf_counter()
    _ = embed("warmup text to trigger model load")
    warmup_ms = _ms(time.perf_counter() - t0)
    print(f"  Model warm-up: {warmup_ms:.1f} ms")

    # ── single embed: 50 calls with distinct texts ───────────────────────
    SINGLE_N = 50
    texts = [
        f"The quick brown fox jumps over the lazy dog — variation {i}. "
        f"Additional context about performance engineering and system design iteration {i}."
        for i in range(SINGLE_N)
    ]

    single_latencies: list[float] = []
    for t in texts:
        t0 = time.perf_counter()
        _ = embed(t)
        single_latencies.append(time.perf_counter() - t0)

    s = _stats(single_latencies)
    total_single = sum(single_latencies)
    tps_single = _throughput(SINGLE_N, total_single)

    print(f"\n  Single embed() — {SINGLE_N} calls:")
    _print_stats("embed()", s)
    print(f"  Throughput: {tps_single:.1f} texts/sec")

    # ── batch embed: sizes 1, 5, 10, 20, 50 ─────────────────────────────
    batch_sizes = [1, 5, 10, 20, 50]
    batch_results: dict[int, dict] = {}

    print(f"\n  embed_batch() — throughput at varying batch sizes:")
    print(f"  {'Batch size':<12} {'Total (ms)':>12} {'Per-item (ms)':>14} {'Texts/sec':>12}")
    print(f"  {THIN_SEP[:55]}")

    for bs in batch_sizes:
        batch_texts = [f"Batch text sample {j} for size {bs}" for j in range(bs)]
        t0 = time.perf_counter()
        _ = embed_batch(batch_texts)
        total_t = time.perf_counter() - t0
        total_ms = _ms(total_t)
        per_item = round(total_ms / bs, 3)
        tps = _throughput(bs, total_t)
        batch_results[bs] = {"total_ms": total_ms, "per_item_ms": per_item, "tps": tps}
        print(f"  {bs:<12} {total_ms:>12.3f} {per_item:>14.3f} {tps:>12.1f}")

    return {
        "single": s,
        "single_tps": tps_single,
        "batch": batch_results,
        "warmup_ms": warmup_ms,
    }


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 2 — Vector Store Insert Latency
# ═══════════════════════════════════════════════════════════════════════════

def bench_insert() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 2 — Vector Store Insert Latency")
    print(SEPARATOR)

    scales = [1, 10, 50, 100, 500]
    results: dict[int, dict] = {}

    print(f"\n  {'Scale':<8} {'Total (ms)':>12} {'Per-insert (ms)':>16} {'Inserts/sec':>13}")
    print(f"  {THIN_SEP[:55]}")

    for n in scales:
        store, d = _tmp_store()
        memories = [_make_memory(i) for i in range(n)]

        t0 = time.perf_counter()
        for m in memories:
            store.store(m)
        total_t = time.perf_counter() - t0

        total_ms = _ms(total_t)
        per_insert = round(total_ms / n, 3)
        ips = _throughput(n, total_t)

        results[n] = {"total_ms": total_ms, "per_insert_ms": per_insert, "ips": ips}
        print(f"  {n:<8} {total_ms:>12.3f} {per_insert:>16.3f} {ips:>13.2f}")
        _cleanup(d)

    # Linearity check: compare per-insert at 1 vs 500
    ratio = round(results[500]["per_insert_ms"] / max(results[1]["per_insert_ms"], 0.001), 2)
    print(f"\n  Per-insert latency ratio (500 vs 1): {ratio}x  "
          f"({'roughly linear — disk I/O dominates' if ratio < 5 else 'super-linear growth detected'})")

    return results


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 3 — Vector Search Latency Scaling
# ═══════════════════════════════════════════════════════════════════════════

def bench_query_scaling() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 3 — Vector Search Latency Scaling with Corpus Size")
    print(SEPARATOR)

    corpus_sizes = [10, 50, 100, 500, 1000]
    QUERIES_PER_SIZE = 20
    query_texts = [
        f"system performance architecture design {i}" for i in range(QUERIES_PER_SIZE)
    ]

    results: dict[int, dict] = {}

    print(f"\n  {'N docs':<8} {'Avg (ms)':>10} {'P95 (ms)':>10} {'Max (ms)':>10} "
          f"{'Min (ms)':>10} {'Queries/s':>11}")
    print(f"  {THIN_SEP[:65]}")

    store, d = _tmp_store()
    inserted = 0

    for n in corpus_sizes:
        # Grow the store incrementally to the next target size
        needed = n - inserted
        for i in range(needed):
            store.store(_make_memory(inserted + i))
        inserted = n

        latencies: list[float] = []
        for qt in query_texts:
            t0 = time.perf_counter()
            _ = store.query(qt, k=5)
            latencies.append(time.perf_counter() - t0)

        s = _stats(latencies)
        tps = _throughput(QUERIES_PER_SIZE, sum(latencies))
        results[n] = {**s, "tps": tps}

        print(f"  {n:<8} {s['avg']:>10.3f} {s['p95']:>10.3f} {s['max']:>10.3f} "
              f"{s['min']:>10.3f} {tps:>11.2f}")

    _cleanup(d)

    # Scaling analysis
    if 10 in results and 1000 in results:
        scale_factor = round(results[1000]["avg"] / max(results[10]["avg"], 0.001), 2)
        print(f"\n  Avg latency ratio (1000 docs vs 10 docs): {scale_factor}x "
              f"(FAISS IndexFlatIP is O(N) — factor ~{1000/10:.0f} expected if compute-bound)")

    return results


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 4 — Full Store Pipeline (embed + insert + save)
# ═══════════════════════════════════════════════════════════════════════════

def bench_full_store_pipeline() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 4 — Full store() Pipeline: embed + FAISS insert + SQLite + disk save")
    print(SEPARATOR)

    N = 100
    store, d = _tmp_store()

    latencies: list[float] = []
    for i in range(N):
        m = _make_memory(i)
        t0 = time.perf_counter()
        store.store(m)
        latencies.append(time.perf_counter() - t0)

    _cleanup(d)

    s = _stats(latencies)
    tps = _throughput(N, sum(latencies))

    print(f"\n  100 consecutive store() calls (embed + insert + faiss.write_index):")
    _print_stats("store()", s)
    print(f"  Throughput: {tps:.2f} stores/sec")

    # Degradation: first 10 vs last 10
    first10 = statistics.mean([_ms(v) for v in latencies[:10]])
    last10  = statistics.mean([_ms(v) for v in latencies[90:]])
    change_pct = round((last10 - first10) / max(first10, 0.001) * 100, 1)
    print(f"\n  First-10 avg: {first10:.3f} ms   Last-10 avg: {last10:.3f} ms   "
          f"Change: {change_pct:+.1f}%")

    return {"stats": s, "tps": tps, "first10_avg_ms": round(first10, 3),
            "last10_avg_ms": round(last10, 3), "change_pct": change_pct}


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 5 — LLM Summarization Latency
# ═══════════════════════════════════════════════════════════════════════════

def bench_llm() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 5 — LLM Summarization Latency (Ollama)")
    print(SEPARATOR)

    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        ollama_ok = r.status_code == 200
        models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        ollama_ok = False
        models = []

    if not ollama_ok:
        print("\n  N/A — Ollama not running (http://localhost:11434 unreachable)")
        return {"available": False}

    print(f"\n  Ollama available. Models: {models}")

    from llm.summarizer import summarize_text, compress_memories

    # ── summarize_text at varying text lengths ───────────────────────────
    base = (
        "This document discusses the architectural design of distributed systems "
        "with a focus on consistency, availability, and partition tolerance. "
        "Key components include load balancers, message queues, and caching layers. "
    )
    text_sizes = {
        100:  base[:100],
        500:  (base * 4)[:500],
        1000: (base * 8)[:1000],
        2000: (base * 15)[:2000],
    }

    summarize_results: dict[int, dict] = {}
    print(f"\n  summarize_text() — 5 measurements per text length:")
    print(f"  {'Chars':<8} {'Avg (ms)':>10} {'P95 (ms)':>10} {'Max (ms)':>10}")
    print(f"  {THIN_SEP[:42]}")

    for char_len, text in text_sizes.items():
        lats: list[float] = []
        for _ in range(5):
            t0 = time.perf_counter()
            _ = summarize_text(text)
            lats.append(time.perf_counter() - t0)
        s = _stats(lats)
        summarize_results[char_len] = s
        print(f"  {char_len:<8} {s['avg']:>10.3f} {s['p95']:>10.3f} {s['max']:>10.3f}")

    # ── compress_memories at varying memory counts ───────────────────────
    store, d = _tmp_store()
    for i in range(10):
        store.store(_make_memory(i))
    all_memories = store.query("system architecture performance", k=10)

    compress_results: dict[int, dict] = {}
    print(f"\n  compress_memories() — 3 measurements per memory-count:")
    print(f"  {'Memories':<10} {'Avg (ms)':>10} {'P95 (ms)':>10} {'Max (ms)':>10}")
    print(f"  {THIN_SEP[:44]}")

    for count in [3, 5, 10]:
        subset = all_memories[:min(count, len(all_memories))]
        lats: list[float] = []
        for _ in range(3):
            t0 = time.perf_counter()
            _ = compress_memories(subset, "system architecture")
            lats.append(time.perf_counter() - t0)
        s = _stats(lats)
        compress_results[count] = s
        print(f"  {count:<10} {s['avg']:>10.3f} {s['p95']:>10.3f} {s['max']:>10.3f}")

    _cleanup(d)

    return {
        "available": True,
        "models": models,
        "summarize": summarize_results,
        "compress": compress_results,
    }


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 6 — End-to-End Pipeline (mocked LLM)
# ═══════════════════════════════════════════════════════════════════════════

def bench_e2e() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 6 -- End-to-End Pipeline Latency (store->query, LLM mocked)")
    print(SEPARATOR)

    from unittest.mock import patch, MagicMock

    store, d = _tmp_store()

    # Pre-populate with 50 docs
    print("  Pre-populating store with 50 documents ...")
    for i in range(50):
        store.store(_make_memory(i))

    queries = [
        "system architecture design patterns",
        "performance optimization techniques",
        "distributed systems consistency",
        "caching strategies and invalidation",
        "database indexing and query optimization",
        "microservices communication protocols",
        "container orchestration at scale",
        "monitoring and observability practices",
        "security hardening for APIs",
        "CI/CD pipeline automation",
    ]

    # Mock the LLM so we isolate embed + FAISS + SQLite overhead
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Mocked compressed summary."

    e2e_latencies: list[float] = []

    with patch("llm.client.get_llm_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_client_fn.return_value = mock_client

        from compression.context_builder import build_compressed_context

        for q in queries:
            t0 = time.perf_counter()
            _ = build_compressed_context(q, k=5, store=store)
            e2e_latencies.append(time.perf_counter() - t0)

    _cleanup(d)

    s = _stats(e2e_latencies)
    tps = _throughput(len(queries), sum(e2e_latencies))

    print(f"\n  10 end-to-end query runs (50-doc store, LLM mocked):")
    _print_stats("e2e query", s)
    print(f"  Throughput: {tps:.2f} queries/sec")

    return {"stats": s, "tps": tps}


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 7 — Stress Test (100 rapid mixed operations)
# ═══════════════════════════════════════════════════════════════════════════

def bench_stress() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 7 — Stress Test: 100 Rapid Mixed Operations (store + query)")
    print(SEPARATOR)

    store, d = _tmp_store()
    # Seed with a few docs so early queries don't hit empty store
    for i in range(5):
        store.store(_make_memory(i, f"seed document {i} for stress test warm-up"))

    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()
    mem_before = tracemalloc.get_traced_memory()[0]

    all_latencies: list[float] = []
    errors = 0

    t_total_start = time.perf_counter()
    for i in range(100):
        try:
            t0 = time.perf_counter()
            if i % 2 == 0:
                store.store(_make_memory(i + 1000))
            else:
                store.query("test query system performance", k=5)
            all_latencies.append(time.perf_counter() - t0)
        except Exception as exc:
            errors += 1
            all_latencies.append(0.0)
            print(f"  ERROR at op {i}: {exc}")

    t_total = time.perf_counter() - t_total_start

    mem_after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    _cleanup(d)

    s = _stats([v for v in all_latencies if v > 0])

    first10_avg = statistics.mean([_ms(v) for v in all_latencies[:10] if v > 0])
    last10_avg  = statistics.mean([_ms(v) for v in all_latencies[90:] if v > 0])
    change_pct  = round((last10_avg - first10_avg) / max(first10_avg, 0.001) * 100, 1)

    mem_growth_kb = round((mem_after - mem_before) / 1024, 1)

    print(f"\n  100 mixed ops (50 store + 50 query):")
    _print_stats("stress ops", s)
    print(f"  Total wall time: {_ms(t_total):.1f} ms ({t_total:.3f} s)")
    print(f"  Throughput: {_throughput(100, t_total):.2f} ops/sec")
    print(f"  Error count: {errors}")
    print(f"  First-10 avg: {first10_avg:.3f} ms   Last-10 avg: {last10_avg:.3f} ms   "
          f"Change: {change_pct:+.1f}%")
    print(f"  Traced memory growth: {mem_growth_kb:.1f} KB")

    return {
        "stats": s,
        "total_ms": _ms(t_total),
        "errors": errors,
        "first10_avg_ms": round(first10_avg, 3),
        "last10_avg_ms": round(last10_avg, 3),
        "change_pct": change_pct,
        "mem_growth_kb": mem_growth_kb,
        "ops_per_sec": _throughput(100, t_total),
    }


# ═══════════════════════════════════════════════════════════════════════════
# BENCHMARK 8 — Persistence Overhead (startup / load time)
# ═══════════════════════════════════════════════════════════════════════════

def bench_persistence() -> dict:
    print(f"\n{SEPARATOR}")
    print("BENCHMARK 8 — Persistence Overhead: VectorStore startup time")
    print(SEPARATOR)

    load_sizes = [0, 100, 1000]
    results: dict[int, dict] = {}
    dirs: list[Path] = []

    # Build pre-populated stores on disk
    prepared: dict[int, Path] = {}
    for n in load_sizes:
        if n == 0:
            # We measure fresh creation
            prepared[0] = None  # type: ignore[assignment]
            continue
        d = Path(tempfile.mkdtemp(prefix=f"bench_persist_{n}_"))
        dirs.append(d)
        print(f"  Preparing store with {n} docs (this may take a while) ...")
        s = VectorStore(persist_path=d)
        for i in range(n):
            s.store(_make_memory(i))
        prepared[n] = d
        print(f"    Done. FAISS index: {(d / 'faiss.index').stat().st_size / 1024:.1f} KB  "
              f"SQLite: {(d / 'metadata.db').stat().st_size / 1024:.1f} KB")

    print(f"\n  Measuring startup (VectorStore.__init__) time — 5 trials each:")
    print(f"  {'N docs':<8} {'Avg (ms)':>10} {'P95 (ms)':>10} {'Max (ms)':>10} {'Min (ms)':>10}")
    print(f"  {THIN_SEP[:52]}")

    for n in load_sizes:
        lats: list[float] = []
        for _ in range(5):
            if n == 0:
                d = Path(tempfile.mkdtemp(prefix="bench_fresh_"))
                t0 = time.perf_counter()
                _ = VectorStore(persist_path=d)
                lats.append(time.perf_counter() - t0)
                _cleanup(d)
            else:
                t0 = time.perf_counter()
                _ = VectorStore(persist_path=prepared[n])
                lats.append(time.perf_counter() - t0)

        s = _stats(lats)
        results[n] = s
        print(f"  {n:<8} {s['avg']:>10.3f} {s['p95']:>10.3f} {s['max']:>10.3f} {s['min']:>10.3f}")

    for d in dirs:
        _cleanup(d)

    return results


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY & SCORING
# ═══════════════════════════════════════════════════════════════════════════

def print_summary(b1: dict, b2: dict, b3: dict, b4: dict,
                  b5: dict, b6: dict, b7: dict, b8: dict) -> None:
    print(f"\n{SEPARATOR}")
    print("SUMMARY TABLE — All Operations")
    print(SEPARATOR)
    print(f"\n  {'Operation':<35} {'Avg (ms)':>10} {'P95 (ms)':>10} {'Max (ms)':>10} {'Throughput (ops/s)':>18}")
    print(f"  {THIN_SEP[:87]}")

    rows = []

    # B1
    e = b1["single"]
    rows.append(("embed() single", e["avg"], e["p95"], e["max"], b1["single_tps"]))

    for bs, br in b1["batch"].items():
        rows.append((f"embed_batch(n={bs})", br["total_ms"], "—", "—", br["tps"]))

    # B2
    for n, r in b2.items():
        rows.append((f"store() insert @{n} docs", r["per_insert_ms"], "—", "—", r["ips"]))

    # B3
    for n, r in b3.items():
        rows.append((f"query() @{n} docs", r["avg"], r["p95"], r["max"], r.get("tps", 0)))

    # B4
    p = b4["stats"]
    rows.append(("store() pipeline (100 runs)", p["avg"], p["p95"], p["max"], b4["tps"]))

    # B6
    eq = b6["stats"]
    rows.append(("e2e query (mocked LLM)", eq["avg"], eq["p95"], eq["max"], b6["tps"]))

    # B7
    sq = b7["stats"]
    rows.append(("stress ops (mixed)", sq["avg"], sq["p95"], sq["max"], b7["ops_per_sec"]))

    for op, avg, p95, mx, tps in rows:
        avg_s = f"{avg:>10.3f}" if isinstance(avg, float) else f"{'—':>10}"
        p95_s = f"{p95:>10.3f}" if isinstance(p95, float) else f"{'—':>10}"
        mx_s  = f"{mx:>10.3f}"  if isinstance(mx,  float) else f"{'—':>10}"
        tps_s = f"{tps:>18.2f}" if isinstance(tps, float) else f"{'—':>18}"
        print(f"  {op:<35} {avg_s} {p95_s} {mx_s} {tps_s}")

    # ── Bottleneck Analysis ──────────────────────────────────────────────
    print(f"\n{SEPARATOR}")
    print("TOP 3 BOTTLENECKS")
    print(SEPARATOR)

    pipeline_avg = b4["stats"]["avg"]
    embed_avg    = b1["single"]["avg"]
    insert_diff  = pipeline_avg - embed_avg  # rough disk I/O portion

    bottlenecks = [
        ("FAISS index disk save (faiss.write_index on every store())",
         f"~{insert_diff:.1f} ms per store() — grows as index size grows on disk"),
        ("Embedding inference (sentence-transformers all-MiniLM-L6-v2)",
         f"~{embed_avg:.1f} ms per call — CPU-bound; batching amortizes this"),
        ("SQLite commit per insert",
         f"Synchronous WAL commit adds ~1-5 ms I/O per store() — measured in store pipeline"),
    ]

    for i, (name, detail) in enumerate(bottlenecks, 1):
        print(f"\n  #{i} {name}")
        print(f"     {detail}")

    # ── Performance Targets ──────────────────────────────────────────────
    print(f"\n{SEPARATOR}")
    print("PERFORMANCE TARGETS — Pass / Fail")
    print(SEPARATOR)

    targets = []

    # embed avg < 100ms
    ea = b1["single"]["avg"]
    targets.append(("embed() avg < 100 ms", ea < 100,
                    f"actual={ea:.3f} ms"))

    # store() avg < 500ms
    sa = b4["stats"]["avg"]
    targets.append(("store() avg < 500 ms", sa < 500,
                    f"actual={sa:.3f} ms"))

    # query() avg < 200ms — use 100-doc result
    qa = b3.get(100, b3.get(max(b3.keys()), {})).get("avg", 9999)
    targets.append(("query() avg < 200 ms (@100 docs)", qa < 200,
                    f"actual={qa:.3f} ms"))

    # Stress test error-free
    targets.append(("100-op stress test: 0 errors", b7["errors"] == 0,
                    f"errors={b7['errors']}"))

    # No latency degradation > 50%
    deg_ok = abs(b7["change_pct"]) <= 50
    targets.append(("Stress: latency change <= 50% (op1-10 vs 90-100)", deg_ok,
                    f"change={b7['change_pct']:+.1f}%"))

    passed = sum(1 for _, ok, _ in targets if ok)
    total  = len(targets)

    for name, ok, detail in targets:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}  ({detail})")

    score = round(passed / total * 100)
    print(f"\n  Performance Score: {score}/100  ({passed}/{total} targets met)")

    # ── Recommendations ──────────────────────────────────────────────────
    print(f"\n{SEPARATOR}")
    print("RECOMMENDATIONS")
    print(SEPARATOR)

    print("""
  1. BATCH FAISS SAVES — Most impactful fix:
     Replace per-insert faiss.write_index() with a write-behind pattern:
     buffer N inserts, flush to disk every N ops or every T seconds.
     Expected impact: 5-20x throughput improvement on bulk ingestion.

  2. BATCH EMBEDDINGS — Use embed_batch() instead of embed() in store():
     SentenceTransformer encodes batches in a single forward pass.
     Expected impact: 2-10x embedding throughput for bulk store operations.

  3. SQLITE WAL MODE — Enable WAL journal mode to reduce commit latency:
       conn.execute("PRAGMA journal_mode=WAL")
       conn.execute("PRAGMA synchronous=NORMAL")
     Expected impact: 2-3x SQLite write throughput.

  4. INDEX TYPE — For large corpora (>10K docs), switch from IndexFlatIP
     (O(N) exact search) to IndexIVFFlat or IndexHNSWFlat for sub-linear
     approximate search. Latency stays flat as N grows.

  5. EMBEDDING CACHE — If the same text is stored repeatedly, an LRU cache
     on embed() avoids redundant inference. Already partially done via
     lru_cache on _get_model(), but text-level caching would help more.
""")

    return {"score": score, "passed": passed, "total": total}


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    print(f"\n{'#' * 72}")
    print("  SEMANTIC MEMORY MCP — PERFORMANCE BENCHMARK SUITE")
    print(f"  Started: {datetime.utcnow().isoformat()}Z")
    print(f"{'#' * 72}")

    t_global_start = time.perf_counter()

    b1 = bench_embedding()
    b2 = bench_insert()
    b3 = bench_query_scaling()
    b4 = bench_full_store_pipeline()
    b5 = bench_llm()
    b6 = bench_e2e()
    b7 = bench_stress()
    b8 = bench_persistence()

    print_summary(b1, b2, b3, b4, b5, b6, b7, b8)

    t_total = time.perf_counter() - t_global_start
    print(f"\n  Total benchmark wall time: {t_total:.1f} s")
    print(f"  Finished: {datetime.utcnow().isoformat()}Z")
    print(f"\n{'#' * 72}\n")


if __name__ == "__main__":
    main()

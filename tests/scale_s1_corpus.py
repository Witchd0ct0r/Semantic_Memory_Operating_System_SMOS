"""
S1 — Corpus Scaling Benchmark
Tests ingest throughput, query latency, FAISS index size, SQLite growth,
and memory consumption at 1K / 5K / 10K / 50K / 100K memories.

Bulk-insert path bypasses per-insert FAISS saves for realistic throughput.
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
import tracemalloc
import statistics
from datetime import datetime
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.embeddings import embed, embed_batch
from memory.vector_store import VectorStore
from memory.schemas import MemoryObject

import uuid as uuid_mod

# ---------------------------------------------------------------------------
# Synthetic corpus — 10 topic clusters × repeated variations
# ---------------------------------------------------------------------------
_TEMPLATES = [
    "PostgreSQL uses MVCC for concurrent transaction isolation and ACID guarantees in {v}.",
    "Redis implements a single-threaded event loop with non-blocking I/O for {v} throughput.",
    "Kubernetes schedules pods using bin-packing with resource requests and limits in {v}.",
    "JWT tokens encode base64-URL claims signed with HMAC-SHA256 for stateless auth in {v}.",
    "FastAPI leverages Python type hints and Pydantic v2 for automatic OpenAPI generation in {v}.",
    "Prometheus scrapes metrics endpoints every 15 seconds and stores time-series in {v}.",
    "React reconciles the virtual DOM using a fiber scheduler to minimize mutations in {v}.",
    "Gradient descent minimizes cross-entropy loss by computing parameter gradients in {v}.",
    "GitHub Actions triggers CI workflows on push and pull-request events in {v}.",
    "Service mesh sidecars intercept all inbound and outbound pod traffic via Envoy in {v}.",
]


def _generate_corpus(n: int) -> list[str]:
    out = []
    for i in range(n):
        tmpl = _TEMPLATES[i % len(_TEMPLATES)]
        out.append(tmpl.format(v=f"version {i // len(_TEMPLATES) + 1}, iteration {i}"))
    return out


# ---------------------------------------------------------------------------
# Fast bulk insert — bypasses per-insert FAISS save
# ---------------------------------------------------------------------------
def _bulk_insert(store: VectorStore, texts: list[str], batch_size: int = 128) -> dict:
    t_embed_start = time.perf_counter()

    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vecs = embed_batch(batch)
        all_vectors.extend(vecs)

    t_embed_end = time.perf_counter()

    arr = np.array(all_vectors, dtype=np.float32)
    faiss.normalize_L2(arr)

    t_db_start = time.perf_counter()
    now_iso = datetime.utcnow().isoformat()

    with store._lock:
        # Get highest existing row_id
        max_row = store._db.execute("SELECT MAX(row_id) FROM memories").fetchone()[0]
        base_id = (max_row or 0)

        rows = [
            (str(uuid_mod.uuid4()), "doc", text, now_iso, "", "hot")
            for text in texts
        ]
        store._db.executemany(
            "INSERT INTO memories (uuid, type, content, timestamp, tags, tier) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        store._db.commit()

        row_ids = np.arange(base_id + 1, base_id + 1 + len(texts), dtype=np.int64)
        store._index.add_with_ids(arr, row_ids)
        store._insert_count += len(texts)

        t_db_end = time.perf_counter()
        t_save_start = time.perf_counter()
        store._save_index()
        t_save_end = time.perf_counter()

    return {
        "embed_sec": t_embed_end - t_embed_start,
        "db_sec": t_db_end - t_db_start,
        "save_sec": t_save_end - t_save_start,
        "total_sec": t_save_end - t_embed_start,
    }


def _measure_query_latency(store: VectorStore, queries: list[str], k: int = 5) -> dict:
    lats = []
    for q in queries:
        t0 = time.perf_counter()
        store.query(q, k=k)
        lats.append((time.perf_counter() - t0) * 1000)
    sv = sorted(lats)
    n = len(sv)
    return {
        "avg_ms": round(statistics.mean(sv), 2),
        "p50_ms": round(sv[n // 2], 2),
        "p95_ms": round(sv[int(n * 0.95)], 2),
        "p99_ms": round(sv[int(n * 0.99)], 2),
        "max_ms": round(sv[-1], 2),
    }


def _dir_size_kb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / 1024, 1)


def _file_size_kb(p: Path) -> float:
    if not p.exists():
        return 0.0
    return round(p.stat().st_size / 1024, 1)


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------
print("Warming up embedding model...")
_ = embed("warmup sentence for model initialization")
print("Warmup complete.\n")

SCALES = [1_000, 5_000, 10_000, 50_000, 100_000]
QUERY_SAMPLE = [
    "PostgreSQL transaction isolation",
    "Redis key-value throughput",
    "Kubernetes pod scheduling",
    "JWT authentication token",
    "FastAPI Python API framework",
    "Prometheus metrics scraping",
    "React virtual DOM reconciliation",
    "gradient descent optimization",
    "GitHub Actions CI pipeline",
    "service mesh Envoy proxy",
]

results = []

print(f"{'Scale':>8}  {'Ingest(s)':>10}  {'Embed(s)':>9}  {'Save(s)':>8}  "
      f"{'Throughput':>12}  {'FAISS(KB)':>10}  {'SQLite(KB)':>11}  "
      f"{'q_avg(ms)':>10}  {'q_p95(ms)':>10}  {'q_p99(ms)':>10}  {'RSS(MB)':>8}")
print("-" * 130)

for scale in SCALES:
    data_dir = Path(tempfile.mkdtemp(prefix=f"s1_{scale}_"))
    store = VectorStore(persist_path=data_dir)

    corpus = _generate_corpus(scale)

    tracemalloc.start()
    snap0 = tracemalloc.take_snapshot()

    timing = _bulk_insert(store, corpus)

    snap1 = tracemalloc.take_snapshot()
    tracemalloc.stop()

    mem_kb = sum(x.size_diff for x in snap1.compare_to(snap0, "lineno"))
    mem_mb = round(mem_kb / 1024 / 1024, 1)

    q_lats = _measure_query_latency(store, QUERY_SAMPLE * 2)

    faiss_kb = _file_size_kb(data_dir / "faiss.index")
    sqlite_kb = _file_size_kb(data_dir / "metadata.db")
    throughput = round(scale / timing["total_sec"], 1)

    row = {
        "scale": scale,
        "ingest_sec": round(timing["total_sec"], 2),
        "embed_sec": round(timing["embed_sec"], 2),
        "db_sec": round(timing["db_sec"], 2),
        "save_sec": round(timing["save_sec"], 2),
        "throughput_dps": throughput,
        "faiss_kb": faiss_kb,
        "sqlite_kb": sqlite_kb,
        "query": q_lats,
        "mem_mb": mem_mb,
    }
    results.append(row)

    print(
        f"{scale:>8,}  {timing['total_sec']:>10.2f}  {timing['embed_sec']:>9.2f}  "
        f"{timing['save_sec']:>8.2f}  {throughput:>12.1f}  {faiss_kb:>10.1f}  "
        f"{sqlite_kb:>11.1f}  {q_lats['avg_ms']:>10.2f}  {q_lats['p95_ms']:>10.2f}  "
        f"{q_lats['p99_ms']:>10.2f}  {mem_mb:>8.1f}"
    )

    # Free memory before next iteration
    del store
    del corpus

print("\n=== SCALING ANALYSIS ===")
if len(results) >= 2:
    r0, rN = results[0], results[-1]
    scale_factor = rN["scale"] / r0["scale"]
    query_factor = rN["query"]["avg_ms"] / max(r0["query"]["avg_ms"], 0.001)
    faiss_factor = rN["faiss_kb"] / max(r0["faiss_kb"], 1)
    print(f"  Scale factor {r0['scale']:,} -> {rN['scale']:,}: {scale_factor}x")
    print(f"  Query latency growth: {query_factor:.2f}x (ideal: O(n) = {scale_factor}x)")
    print(f"  FAISS index growth:   {faiss_factor:.2f}x (ideal: linear = {scale_factor}x)")

print("\n=== ARCHITECTURAL LIMITS ===")
last = results[-1]
print(f"  Max tested scale:   {last['scale']:,} memories")
print(f"  FAISS index at max: {last['faiss_kb']:,.1f} KB = {last['faiss_kb']/1024:.1f} MB")
print(f"  SQLite at max:      {last['sqlite_kb']:,.1f} KB = {last['sqlite_kb']/1024:.1f} MB")
print(f"  Query p95 at max:   {last['query']['p95_ms']} ms")
print(f"  Ingest throughput:  {last['throughput_dps']} docs/sec (bulk path)")

# Extrapolation to 1M
if len(results) >= 2:
    r1, r2 = results[-2], results[-1]
    if r1["scale"] > 0 and r2["scale"] > r1["scale"]:
        q_slope = (r2["query"]["avg_ms"] - r1["query"]["avg_ms"]) / (r2["scale"] - r1["scale"])
        predicted_1m = r2["query"]["avg_ms"] + q_slope * (1_000_000 - r2["scale"])
        print(f"\n  Projected query avg @ 1M docs: ~{predicted_1m:.1f} ms (linear extrapolation)")

print("\nS1 complete.")

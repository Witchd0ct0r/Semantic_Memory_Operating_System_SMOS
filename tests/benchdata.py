"""Hand-built benchmark result dicts for the analysis, view and page tests (the shape runner.run() produces)."""
from __future__ import annotations

from typing import Optional


def make_result(
    sizes: Optional[list[int]] = None,
    p95s: Optional[list[float]] = None,
    *,
    vector_ms: float = 2.0,
    embed_ms: float = 30.0,
    other_ms: float = 8.0,
    recall10: float = 0.97,
    recall50: float = 0.95,
    budget_ms: float = 200.0,
    sweep: Optional[list[dict]] = None,
    startup_s: float = 5.0,
    ingest_rate: float = 80.0,
) -> dict:
    sizes = sizes or [1000, 5000, 10000, 25000]
    p95s = p95s or [20.0, 25.0, 30.0, 38.0]
    total = vector_ms + embed_ms + other_ms
    scaling = []
    for size, p95 in zip(sizes, p95s):
        scaling.append({
            "segments": size,
            "ingest": {"seg_per_s": ingest_rate, "seconds": size / ingest_rate, "segments": size, "quarantined": 0},
            "stages": {"embed_ms": embed_ms, "vector_ms": vector_ms, "bm25_ms": other_ms / 2, "graph_ms": other_ms / 4,
                       "sqlite_ms": other_ms / 8, "fusion_ms": other_ms / 8, "total_ms": total},
            "hnsw": {"recall10": recall10, "recall50": recall50, "build_s": 3.0, "mem_bytes": 40_000_000,
                     "query_ms": 0.5, "flat_ms": vector_ms},
            "quality": {"hit1": 0.7, "hit10": 0.95, "mrr": 0.8},
            "latency": {"cold_ms": p95 * 3, "query": {"n": 50, "mean": p95 * 0.6, "p50": p95 * 0.6, "p95": p95, "p99": p95 * 1.5,
                                                      "max": p95 * 2},
                        "node_text": {"n": 20, "p50": 3.0, "p95": 5.0, "p99": 6.0},
                        "post_doc": {"n": 5, "p50": 100.0, "p95": 150.0, "p99": 160.0}},
            "startup_s": startup_s,
            "disk_bytes": size * 4000,
            "index_bytes": size * 1600,
            "rss_bytes": 600_000_000 + size * 2000,
        })
    sweep = sweep if sweep is not None else [
        {"clients": 1, "requests": 100, "errors": 0, "rps": 20.0, "p50_ms": 40.0, "p95_ms": 50.0, "p99_ms": 60.0,
         "client_cpu_share": 0.1, "client_limited": False},
        {"clients": 4, "requests": 300, "errors": 0, "rps": 40.0, "p50_ms": 90.0, "p95_ms": 120.0, "p99_ms": 140.0,
         "client_cpu_share": 0.2, "client_limited": False},
        {"clients": 16, "requests": 330, "errors": 0, "rps": 41.0, "p50_ms": 380.0, "p95_ms": 450.0, "p99_ms": 500.0,
         "client_cpu_share": 0.3, "client_limited": False},
    ]
    return {
        "schema": 1, "preset": "standard", "seed": 1, "budget_ms": budget_ms, "sizes": sizes,
        "scaling": scaling, "sweep": sweep,
        "mixed": {"clients": 4, "read_p95_alone_ms": 120.0, "read_p95_with_writer_ms": 180.0, "writer_docs": 12},
        "hardware": {"cpu_model": "Test CPU", "cpu_cores": 4, "mem_total": 16_000_000_000, "faiss": "1.14",
                     "python": "3.13", "embedding_model": "all-MiniLM-L6-v2"},
        "warnings": [], "started_at": "2026-10-30T09:00:00+00:00", "finished_at": "2026-10-30T09:20:00+00:00",
        "cleanup": "verified",
    }

"""In-process benchmark phases. They open the store directly, so no server may be running on the same directory."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import faiss
import numpy as np

from smos.bench.corpus import CorpusBuilder, Probe
from smos.bench.stats import percentile
from smos.compression.context_builder import _CANDIDATE_MULTIPLIER, _rrf_merge, _term_overlap
from smos.ingest.contract import IngestionPayload
from smos.memory.embeddings import embed
from smos.memory.lifecycle import LifecycleManager
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH = 32, 200, 64
_DIM = 384
_MAX_RECALL_QUERIES = 200


def open_store(path: Path) -> VectorStore:
    """A store wired like the server's (lifecycle dedup on insert), so ingest costs are the real ones."""
    store = VectorStore(persist_path=Path(path))
    store._lifecycle_callback = LifecycleManager(store).on_insert
    return store


def ingest_to(store: VectorStore, builder: CorpusBuilder, target_segments: int) -> dict:
    """Ingest the corpus' next documents through the same function the API calls, until `target_segments`."""
    documents = builder.next_documents(target_segments)
    before = store.count()
    quarantined = 0
    started = time.perf_counter()
    for doc in documents:
        result = do_ingest_document_schema(IngestionPayload(**doc), store)
        if result.get("status") == "quarantined":
            quarantined += 1
    seconds = time.perf_counter() - started
    after = store.count()
    added = after - before
    return {
        "seconds": seconds, "added": added, "segments": after, "documents": len(documents),
        "quarantined": quarantined, "seg_per_s": added / seconds if documents and seconds > 0 else None,
    }


def timed_context(store: VectorStore, query: str, k: int) -> tuple[list[str], dict[str, float]]:
    """Run the retrieval steps of `smos/compression/context_builder.py::build_context` one by one, timing each.

    Returns the ids build_context would return and the milliseconds spent per stage. It calls the same store, BM25,
    fusion and graph functions; test_timed_context_matches_build_context fails if the two ever drift apart.
    """
    clock = time.perf_counter
    t = {"embed_ms": 0.0, "vector_ms": 0.0, "bm25_ms": 0.0, "graph_ms": 0.0, "sqlite_ms": 0.0, "fusion_ms": 0.0}
    started = clock()
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    mark = clock()
    vector = np.array([embed(query)], dtype=np.float32)
    faiss.normalize_L2(vector)
    t["embed_ms"] += (clock() - mark) * 1000

    mark = clock()
    total = store._index.ntotal
    scores, row_ids = store._index.search(vector, min(candidate_k, total)) if total else (None, None)
    t["vector_ms"] += (clock() - mark) * 1000

    vector_results: list[dict] = []
    if total:
        mark = clock()
        hits = [(float(s), int(r)) for s, r in zip(scores[0], row_ids[0]) if r != -1]
        rows = store._rows_by_row_id([r for _s, r in hits])
        for score, row_id in hits:
            row = rows.get(row_id)
            if row is not None:
                vector_results.append({"id": row[1], "content": row[3], "score": score})
        t["sqlite_ms"] += (clock() - mark) * 1000

    mark = clock()
    bm25 = [(uid, s) for uid, s in store.bm25.query(query, k=candidate_k) if s > 0.0]
    t["bm25_ms"] += (clock() - mark) * 1000

    mark = clock()
    merged = _rrf_merge(vector_results, bm25, top_k=k * 2)
    t["fusion_ms"] += (clock() - mark) * 1000

    mark = clock()
    by_id = {r["id"]: r for r in vector_results}
    fetched = store.get_many([uid for uid, _ in merged if uid not in by_id])
    t["sqlite_ms"] += (clock() - mark) * 1000

    mark = clock()
    rrf = {uid: s for uid, s in merged}
    candidates = [r for r in ((by_id.get(uid) or fetched.get(uid)) for uid, _ in merged) if r]
    for c in candidates:
        c["_rrf"], c["_overlap"] = rrf.get(c["id"], 0.0), _term_overlap(query, c.get("content", ""))
    candidates.sort(key=lambda c: (c["_rrf"], c["_overlap"]), reverse=True)
    top = candidates[:k]
    t["fusion_ms"] += (clock() - mark) * 1000

    ids = [c["id"] for c in top]
    if top:
        mark = clock()
        existing, added = set(ids), 0
        for node in store.graph.traverse(top[0]["id"], max_hops=2, edge_types=["references"]):
            if added >= 2:
                break
            if node["type"] != "memory" or node["id"] in existing:
                continue
            record = store.get_by_uuid(node["id"])
            if record is None:
                continue
            ids.append(record["id"])
            existing.add(node["id"])
            added += 1
        t["graph_ms"] += (clock() - mark) * 1000

    t["total_ms"] = (clock() - started) * 1000
    return ids, t


def profile_stages(store: VectorStore, probes: list[Probe], k: int = 10, repeats: int = 3) -> dict:
    """Mean milliseconds per stage over every probe and repeat, plus the wall-clock total and its p95."""
    samples: list[dict[str, float]] = []
    for _ in range(repeats):
        for probe in probes:
            samples.append(timed_context(store, probe.query, k)[1])
    if not samples:
        return {"samples": 0, "total_ms": 0.0, "p95_total_ms": 0.0,
                **{key: 0.0 for key in ("embed_ms", "vector_ms", "bm25_ms", "graph_ms", "sqlite_ms", "fusion_ms")}}
    mean = {key: sum(s[key] for s in samples) / len(samples) for key in samples[0]}
    mean["samples"] = len(samples)
    mean["p95_total_ms"] = percentile([s["total_ms"] for s in samples], 95)
    return mean


def compare_hnsw(store: VectorStore, probes: list[Probe], k_values: tuple[int, ...] = (10, 50)) -> dict:
    """Build an HNSW index over the store's own vectors and compare it with exact search on the probe queries."""
    flat = faiss.downcast_index(store._index.index)
    total = flat.ntotal
    vectors = flat.reconstruct_n(0, total)
    queries = np.array([embed(p.query) for p in probes[:_MAX_RECALL_QUERIES]], dtype=np.float32)
    faiss.normalize_L2(queries)

    built = time.perf_counter()
    hnsw = faiss.IndexHNSWFlat(_DIM, HNSW_M, faiss.METRIC_INNER_PRODUCT)
    hnsw.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
    hnsw.add(vectors)
    hnsw.hnsw.efSearch = HNSW_EF_SEARCH
    build_s = time.perf_counter() - built
    mem_bytes = len(faiss.serialize_index(hnsw))

    def timed_search(index, k: int) -> tuple[np.ndarray, float]:
        found, elapsed = [], 0.0
        for q in queries:
            mark = time.perf_counter()
            _scores, ids = index.search(q.reshape(1, -1), k)
            elapsed += time.perf_counter() - mark
            found.append(ids[0])
        return np.array(found), elapsed / max(len(queries), 1) * 1000

    out: dict = {"build_s": build_s, "mem_bytes": mem_bytes, "segments": total, "queries": len(queries)}
    for k in k_values:
        if total < k or not len(queries):
            out[f"recall{k}"] = None
            continue
        exact, flat_ms = timed_search(flat, k)
        approx, hnsw_ms = timed_search(hnsw, k)
        out[f"recall{k}"] = float(np.mean([len(set(a) & set(e)) / k for a, e in zip(approx, exact)]))
        if k == k_values[0]:
            again, _ms = timed_search(flat, k)
            out["flat_vs_flat_recall10"] = float(np.mean([len(set(a) & set(e)) / k for a, e in zip(again, exact)]))
            out["flat_ms"], out["query_ms"] = flat_ms, hnsw_ms
    out.setdefault("flat_ms", None)
    out.setdefault("query_ms", None)
    return out


def store_footprint(path: Path) -> dict:
    """Bytes on disk of the whole store directory and of the search index file."""
    path = Path(path)
    disk = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    index = path / "faiss.index"
    return {"disk_bytes": disk, "index_bytes": index.stat().st_size if index.exists() else 0}

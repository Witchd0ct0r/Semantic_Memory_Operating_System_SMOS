from __future__ import annotations

import numpy as np

from smos.compression.context_builder import build_context, to_retrieved_context
from smos.memory.embeddings import embed_batch
from smos.memory.exact_retrieval import recall_exact
from smos.memory.vector_store import VectorStore

_VALID_STRATEGIES = {"hybrid", "temporal", "chain", "repo", "exact"}
_CHAIN_EDGE_TYPES = ["evolves_into", "related_to"]


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
    elif strategy == "temporal":
        result = _recall_temporal(query, k, since, until, store)
    elif strategy == "chain":
        result = _recall_chain(seed_id, k, store)
    elif strategy == "exact":
        result = recall_exact(query, k, store)
    else:
        result = build_context(query, k, store).model_dump()

    store.bump_recall(result.get("sources", []))
    return result


def _recall_repo(query: str, k: int, component: str, store: VectorStore) -> dict:
    tags = [component] if component else None
    return build_context(query, k, store, tags=tags).model_dump()


def _recall_temporal(query: str, k: int, since: str, until: str, store: VectorStore) -> dict:
    rows = store.get_by_timestamp_range(since, until)
    if not rows:
        return to_retrieved_context([], store).model_dump()

    contents = [r["content"] for r in rows]
    vecs = np.array(embed_batch([query] + contents), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs /= norms
    query_vec, content_vecs = vecs[0], vecs[1:]
    scores = content_vecs @ query_vec

    scored = sorted(zip(rows, scores), key=lambda t: t[1], reverse=True)[:k]
    records = [
        {**row, "distance": float(1.0 - score), "score": float(score)}
        for row, score in scored
    ]
    return to_retrieved_context(records, store).model_dump()


def _recall_chain(seed_id: str, k: int, store: VectorStore) -> dict:
    nodes = store.graph.traverse(seed_id, max_hops=max(k, 1), edge_types=_CHAIN_EDGE_TYPES)
    memory_ids = [n["id"] for n in nodes if n["type"] == "memory"]
    return {"sources": memory_ids, "chain": nodes, "confidence": 1.0, "mode": "extractive"}

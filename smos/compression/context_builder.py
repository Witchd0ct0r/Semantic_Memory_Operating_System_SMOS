from __future__ import annotations

import re
from typing import Optional

from smos.memory.schemas import CompressedContext
from smos.memory.vector_store import VectorStore
from smos.llm.summarizer import compress_memories_full

_CANDIDATE_MULTIPLIER = 4
_RRF_K = 60  # standard RRF constant


def _term_overlap(query: str, candidate: str) -> float:
    query_tokens = set(re.findall(r"\w+", query.lower()))
    cand_tokens = set(re.findall(r"\w+", candidate.lower()))
    if not query_tokens:
        return 0.0
    return len(query_tokens & cand_tokens) / len(query_tokens)


def _rrf_merge(
    vector_results: list[dict],
    bm25_results: list[tuple[str, float]],
    top_k: int,
) -> list[tuple[str, float]]:
    """Merge two ranked lists via Reciprocal Rank Fusion.

    Returns ordered (uuid, rrf_score) pairs, highest score first.
    """
    scores: dict[str, float] = {}

    for rank, item in enumerate(vector_results):
        uid = item["id"]
        scores[uid] = scores.get(uid, 0.0) + 1.0 / (_RRF_K + rank + 1)

    for rank, (uid, _) in enumerate(bm25_results):
        scores[uid] = scores.get(uid, 0.0) + 1.0 / (_RRF_K + rank + 1)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return ranked


def build_compressed_context(
    query: str,
    k: int,
    store: VectorStore,
    tags: Optional[list[str]] = None,
) -> CompressedContext:
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    # Vector retrieval — explicit tags scope the search; otherwise unfiltered.
    domain_tags = tags or None
    vector_results = store.query_domain(query, k=candidate_k, domain_tags=domain_tags)
    if not vector_results:
        vector_results = store.query(query, k=candidate_k)

    # BM25 retrieval
    bm25_results = store.bm25.query(query, k=candidate_k)

    if not vector_results and not bm25_results:
        return CompressedContext(
            summary="No relevant memories found.",
            sources=[],
            confidence=0.0,
            mode="uncertain",
        )

    # BM25Index.query() pads results with zero-score entries (insertion-order
    # tied) when fewer than k terms match. Those carry no relevance signal, so
    # drop them before fusion to avoid granting them undeserved rank credit.
    bm25_results = [(uid, score) for uid, score in bm25_results if score > 0.0]

    # Hybrid merge via Reciprocal Rank Fusion
    merged = _rrf_merge(vector_results, bm25_results, top_k=k * 2)
    rrf_scores = {uid: score for uid, score in merged}

    # Hydrate merged results: prefer the vector hit (already carries FAISS
    # distance); fall back to the store for BM25-only hits.
    vector_by_id = {r["id"]: r for r in vector_results}
    candidates = []
    for uid, _ in merged:
        if uid in vector_by_id:
            candidates.append(vector_by_id[uid])
        else:
            record = store.get_by_uuid(uid)
            if record:
                candidates.append(record)

    # Final rerank: RRF fused score is the primary ranking signal; term
    # overlap only breaks ties between candidates with equal (or very close)
    # RRF scores.
    for c in candidates:
        c["_rrf_score"] = rrf_scores.get(c.get("id", ""), 0.0)
        c["_final_score"] = _term_overlap(query, c.get("content", ""))
    candidates.sort(key=lambda c: (c["_rrf_score"], c["_final_score"]), reverse=True)
    top = candidates[:k]

    # compress_memories_full() needs a "distance" per memory to derive
    # confidence. BM25-only hits hydrated via get_by_uuid() don't carry a
    # FAISS distance, so default them to a neutral mid-range value.
    memories_for_compression = [
        {
            "id": c.get("id", ""),
            "content": c.get("content", ""),
            "distance": c.get("distance", 1.0),
        }
        for c in top
    ]
    sources = [c.get("id", "") for c in top]

    summary, confidence, mode = compress_memories_full(memories_for_compression, query)

    return CompressedContext(
        summary=summary,
        sources=sources,
        confidence=confidence,
        mode=mode,
    )

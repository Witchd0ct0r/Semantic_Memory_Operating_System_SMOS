from __future__ import annotations

from smos.compression.context_builder import _rrf_merge
from smos.memory.vector_store import VectorStore

_CANDIDATE_MULTIPLIER = 4
_DEFAULT_NEIGHBOR_WINDOW = 1


def _fetch_ranked_candidates(query_text: str, k: int, store: VectorStore) -> list[dict]:
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    vector_results = store.query(query_text, k=candidate_k)
    bm25_results = [
        (uid, score) for uid, score in store.bm25.query(query_text, k=candidate_k) if score > 0.0
    ]
    if not vector_results and not bm25_results:
        return []

    merged = _rrf_merge(vector_results, bm25_results, top_k=k)

    vector_by_id = {r["id"]: r for r in vector_results}
    candidates: list[dict] = []
    for uid, _ in merged:
        if uid in vector_by_id:
            candidates.append(vector_by_id[uid])
        else:
            record = store.get_by_uuid(uid)
            if record is not None:
                candidates.append(record)
    return candidates


def expand_neighbors(
    store: VectorStore, segments: list[dict], window: int = _DEFAULT_NEIGHBOR_WINDOW
) -> list[dict]:
    """Pull up to `window` segments on each side of every input segment, by
    `ordinal` within the same `doc_id` — the replacement for RAG overlap
    (spec §5/§7): a matched segment brings its immediate context without
    duplicating stored text."""
    seen_ids = {s["id"] for s in segments if s.get("id")}
    neighbors: list[dict] = []
    for seg in segments:
        doc_id = seg.get("metadata", {}).get("doc_id")
        ordinal = seg.get("metadata", {}).get("ordinal")
        if doc_id is None or ordinal is None:
            continue
        for offset in range(1, window + 1):
            for neighbor_ordinal in (ordinal - offset, ordinal + offset):
                if neighbor_ordinal < 0:
                    continue
                neighbor = store.get_segment_by_ordinal(doc_id, neighbor_ordinal)
                if neighbor is None or neighbor["id"] in seen_ids:
                    continue
                neighbors.append(neighbor)
                seen_ids.add(neighbor["id"])
    return neighbors


def recall_exact(query_text: str, k: int, store: VectorStore) -> dict:
    """Stage 2 of the two-stage retrieval loop (spec §7): verbatim segments,
    no LLM compression, plus +/-1 neighbor context. Confidence is 1.0
    whenever at least one segment matches — the returned value is the
    record's exact stored text, so there is no summarization uncertainty
    to weigh."""
    candidates = _fetch_ranked_candidates(query_text, k, store)
    if not candidates:
        return {"segments": [], "context": [], "sources": [], "confidence": 0.0, "mode": "uncertain"}

    context = expand_neighbors(store, candidates)
    return {
        "segments": candidates,
        "context": context,
        "sources": [c["id"] for c in candidates],
        "confidence": 1.0,
        "mode": "extractive",
    }

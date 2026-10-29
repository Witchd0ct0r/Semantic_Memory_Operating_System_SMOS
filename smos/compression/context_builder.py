from __future__ import annotations

import re
from typing import Optional

from smos.memory.schemas import RetrievedContext, RetrievedItem
from smos.memory.vector_store import VectorStore

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


def _confidence(records: list[dict]) -> float:
    """Mean-distance confidence in 0..1; BM25-only hits carry no FAISS distance
    and default to a neutral mid-range value."""
    if not records:
        return 0.0
    avg_distance = sum(r.get("distance", 1.0) for r in records) / len(records)
    return max(0.0, min(1.0, 1.0 - (avg_distance / 2.0)))


def _section_id(store: VectorStore, segment_id: str) -> Optional[str]:
    for edge in store.graph.get_edges(segment_id, "in", ["contains"]):
        node = store.graph.get_node(edge["source_id"])
        if node is not None and node["type"] == "section":
            return node["id"]
    return None


def to_retrieved_context(records: list[dict], store: VectorStore) -> RetrievedContext:
    """Exact stored text plus any caller-supplied summary; never synthesized.
    A segment hit also carries the summaries attached to its section and document."""
    ids = [r.get("id", "") for r in records]
    section_ids: dict[str, str] = {}
    doc_ids: list[str] = []
    for r in records:
        doc_id = (r.get("metadata") or {}).get("doc_id")
        if not doc_id:
            continue
        doc_ids.append(doc_id)
        section_id = _section_id(store, r.get("id", ""))
        if section_id:
            section_ids[r.get("id", "")] = section_id
    summaries = store.get_summaries(ids + doc_ids + list(section_ids.values()))
    items = []
    for r in records:
        rid = r.get("id", "")
        meta = r.get("metadata") or {}
        own = summaries.get(rid, {})
        section = summaries.get(section_ids.get(rid, ""), {})
        doc = summaries.get(meta.get("doc_id") or "", {})
        items.append(RetrievedItem(
            id=rid,
            content=r.get("content", ""),
            summary=own.get("summary"),
            summary_source=own.get("source"),
            section_summary=section.get("summary"),
            section_summary_source=section.get("source"),
            document_summary=doc.get("summary"),
            document_summary_source=doc.get("source"),
            score=r.get("score"),
            metadata=meta,
        ))
    return RetrievedContext(
        results=items,
        sources=ids,
        confidence=_confidence(records),
        mode="extractive" if items else "uncertain",
    )


def build_context(
    query: str,
    k: int,
    store: VectorStore,
    tags: Optional[list[str]] = None,
) -> RetrievedContext:
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    # Vector retrieval — explicit tags scope the search; otherwise unfiltered.
    domain_tags = tags or None
    vector_results = store.query_domain(query, k=candidate_k, domain_tags=domain_tags)
    if not vector_results:
        vector_results = store.query(query, k=candidate_k)

    # BM25 retrieval
    bm25_results = store.bm25.query(query, k=candidate_k)

    if not vector_results and not bm25_results:
        return to_retrieved_context([], store)

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
    fetched = store.get_many([uid for uid, _ in merged if uid not in vector_by_id])
    candidates = []
    for uid, _ in merged:
        record = vector_by_id.get(uid) or fetched.get(uid)
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

    # Graph expansion — purely additive: pulls in memories that share an
    # extracted entity with the top-ranked result, without altering the
    # RRF-ranked order of `top` itself. Bounded to 2 extra memories so a
    # densely-connected entity can't blow up the compression input.
    if top:
        graph_neighbors = store.graph.traverse(top[0].get("id", ""), max_hops=2, edge_types=["references"])
        existing_ids = {c.get("id", "") for c in top}
        added = 0
        for node in graph_neighbors:
            if added >= 2:
                break
            if node["type"] != "memory" or node["id"] in existing_ids:
                continue
            record = store.get_by_uuid(node["id"])
            if record is None:
                continue
            top.append(record)
            existing_ids.add(node["id"])
            added += 1

    return to_retrieved_context(top, store)

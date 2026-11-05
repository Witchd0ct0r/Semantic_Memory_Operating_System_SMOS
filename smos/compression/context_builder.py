from __future__ import annotations

import re
from typing import Optional

from smos.memory.schemas import RetrievedContext, RetrievedItem
from smos.memory.tagfilter import TagFilter
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


_BLOCK_KEYS = ("block_ordinal", "block_ordinal_end", "block_char_start", "block_char_end", "block_type")


def _enrich_metadata(meta: dict, hashes: dict[str, str], store: VectorStore) -> dict:
    """Add document_hash and the parser block (range and type) a segment hit came from.
    Every key is always present; null means unknown (no document, or a document ingested before blocks were stored)."""
    meta = dict(meta)
    doc_id = meta.get("doc_id")
    meta["document_hash"] = hashes.get(doc_id) if doc_id else None
    block = end_block = None
    if doc_id and meta.get("char_start") is not None and meta.get("char_end") is not None:
        block = store.block_containing(doc_id, meta["char_start"])
        end_block = store.block_containing(doc_id, max(meta["char_end"] - 1, meta["char_start"])) if block else None
    meta["block_ordinal"] = block["ordinal"] if block else None
    meta["block_ordinal_end"] = end_block["ordinal"] if end_block else None
    meta["block_char_start"] = block["char_start"] if block else None
    meta["block_char_end"] = block["char_end"] if block else None
    meta["block_type"] = block["block_type"] if block else None
    return meta


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
    hashes = store.get_document_hashes(doc_ids) if doc_ids else {}
    items = []
    for r in records:
        rid = r.get("id", "")
        meta = _enrich_metadata(r.get("metadata") or {}, hashes, store)
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
            rank=r.get("_rank"),
            rank_score=r.get("_rrf_score"),
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
    *,
    tags_all: Optional[list[str]] = None,
    tags_none: Optional[list[str]] = None,
    offset: int = 0,
) -> RetrievedContext:
    """Hybrid retrieval. A tag scope (`tags` any-of, `tags_all`, `tags_none`, ANDed) is resolved to the
    eligible memories first and restricts the vector, keyword and graph legs alike: a scoped query
    never returns an out-of-scope item and never falls back to an unfiltered search.
    `offset` pages through the fused ranking (offset + k is capped by the API)."""
    scope = TagFilter.make(any=tags, all=tags_all, none=tags_none)
    eligible = store.resolve_tag_filter(scope)
    candidate_k = max((offset + k) * _CANDIDATE_MULTIPLIER, 20)
    total = len(eligible) if eligible is not None else store.count()
    unknown = store.unknown_tags(list(scope.any) + list(scope.all)) if not scope.is_empty else []

    vector_results = store.query_domain(query, k=candidate_k, eligible=eligible)
    bm25_results = store.bm25.query(query, k=candidate_k, allowed=eligible.uuids if eligible is not None else None)

    def _done(records: list[dict]) -> RetrievedContext:
        ctx = to_retrieved_context(records, store)
        ctx.total, ctx.unknown_tags, ctx.offset = total, unknown, offset
        return ctx

    # BM25Index.query() pads results with zero-score entries (insertion-order
    # tied) when fewer than k terms match. Those carry no relevance signal, so
    # drop them before fusion to avoid granting them undeserved rank credit.
    bm25_results = [(uid, score) for uid, score in bm25_results if score > 0.0]

    if not vector_results and not bm25_results:
        return _done([])

    # Hybrid merge via Reciprocal Rank Fusion
    merged = _rrf_merge(vector_results, bm25_results, top_k=(offset + k) * 2)
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
    # RRF scores; the id makes the order total, so pages neither skip nor repeat.
    for c in candidates:
        c["_rrf_score"] = rrf_scores.get(c.get("id", ""), 0.0)
        c["_final_score"] = _term_overlap(query, c.get("content", ""))
    candidates.sort(key=lambda c: (-c["_rrf_score"], -c["_final_score"], c.get("id", "")))
    top = candidates[offset:offset + k]
    for position, c in enumerate(top, start=offset + 1):
        c["_rank"] = position

    # Graph expansion — purely additive: pulls in memories that share an
    # extracted entity with the top-ranked result, without altering the
    # RRF-ranked order of `top` itself. Bounded to 2 extra memories so a
    # densely-connected entity can't blow up the compression input. First page
    # only, and never outside the tag scope.
    if top and offset == 0:
        graph_neighbors = store.graph.traverse(top[0].get("id", ""), max_hops=2, edge_types=["references"])
        existing_ids = {c.get("id", "") for c in top}
        added = 0
        for node in graph_neighbors:
            if added >= 2:
                break
            if node["type"] != "memory" or node["id"] in existing_ids:
                continue
            if eligible is not None and node["id"] not in eligible.uuids:
                continue
            record = store.get_by_uuid(node["id"])
            if record is None:
                continue
            top.append(record)
            existing_ids.add(node["id"])
            added += 1

    # `score` is cosine similarity for every hit; a keyword-only hit has no vector rank, so compute it.
    missing = [c["id"] for c in top if c.get("score") is None and c.get("id")]
    cosine = store.cosine_scores(query, missing) if missing else {}
    for c in top:
        if c.get("score") is None:
            c["score"] = float(cosine.get(c.get("id", ""), 0.0))
    return _done(top)

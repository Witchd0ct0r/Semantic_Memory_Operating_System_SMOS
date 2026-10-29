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
    fetched = store.get_many([uid for uid, _ in merged if uid not in vector_by_id])
    candidates: list[dict] = []
    for uid, _ in merged:
        record = vector_by_id.get(uid) or fetched.get(uid)
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


def reconstruct(store: VectorStore, node_id: str) -> dict:
    """Rebuild the verbatim text under a `document` or `section` graph node
    (spec §7): fetch its segments, order by `ordinal`, concatenate. Because
    `GraphStore.traverse()` is undirected (see `_wire_containment`'s
    docstring in `smos/ingest/segmenter.py`), a section's 1-hop neighbors
    include its parent document alongside its leaf children — filtering to
    `type == "memory"` excludes the parent without needing a directed
    traversal. `complete` is False when the concatenated segments have a gap
    (their char offsets aren't contiguous) — e.g. a document whose
    containment tree isn't fully wired yet (still mid-checkpoint)."""
    node = store.graph.get_node(node_id)
    if node is None or node["type"] not in ("document", "section"):
        return {
            "node_id": node_id, "text": "", "segment_count": 0,
            "complete": False, "error": "node not found, or not a document/section node",
        }

    direct = store.graph.traverse(node_id, max_hops=1, edge_types=["contains"])
    segment_ids = [n["id"] for n in direct if n["type"] == "memory"]
    for section in (n for n in direct if n["type"] == "section"):
        section_children = store.graph.traverse(section["id"], max_hops=1, edge_types=["contains"])
        segment_ids.extend(n["id"] for n in section_children if n["type"] == "memory")

    fetched = store.get_many(segment_ids)
    records = [fetched.get(sid) for sid in segment_ids]
    records = [r for r in records if r is not None and r["metadata"].get("ordinal") is not None]
    records.sort(key=lambda r: r["metadata"]["ordinal"])

    text = "".join(r["content"] for r in records)
    complete = True
    prev_end = None
    for r in records:
        char_start = r["metadata"].get("char_start")
        if prev_end is not None and char_start != prev_end:
            complete = False
        prev_end = r["metadata"].get("char_end")

    return {
        "node_id": node_id,
        "text": text,
        "segment_count": len(records),
        "complete": complete,
        "error": None,
    }

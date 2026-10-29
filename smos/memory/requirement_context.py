from __future__ import annotations

import json
from collections import deque
from typing import Optional

from smos.compression.context_builder import _rrf_merge
from smos.ingest.contract import derive_doc_id
from smos.memory.exact_retrieval import reconstruct
from smos.memory.vector_store import VectorStore

LINK_EDGE_TYPES = ["depends_on", "parent_of", "member_of"]
NORMATIVE_DOCTYPES = ("legal", "standard")
_MAX_HOPS = 2
_NEAR_DUPLICATE_THRESHOLD = 0.90
_CANDIDATE_POOL_PER_K = 100
_MIN_CANDIDATE_POOL = 1000
_DEFAULT_TOKEN_BUDGET = 4000
_DEFAULT_K = 5
_SECTIONS = ("linked", "peers", "normative")

# (edge_type, direction relative to the node we walked FROM) -> label
_RELATION_LABELS = {
    ("depends_on", "out"): "upstream (depends_on)",
    ("depends_on", "in"): "downstream (depends_on)",
    ("parent_of", "out"): "child (parent_of)",
    ("parent_of", "in"): "parent (parent_of)",
    ("member_of", "out"): "member of (member_of)",
    ("member_of", "in"): "has member (member_of)",
}
_EDGE_ORDER = {t: i for i, t in enumerate(LINK_EDGE_TYPES)}


def _placeholder(node: Optional[dict]) -> bool:
    return bool(node and node.get("metadata", {}).get("placeholder"))


def _doc_text(store: VectorStore, doc_id: str) -> str:
    return reconstruct(store, doc_id).get("text", "")


def _cost(text: str) -> int:
    return len(text) // 4 + 1


def _fit(items: list[dict], budget_tokens: int) -> tuple[list[dict], bool]:
    """Keep items in order while they fit `budget_tokens`. If even the first
    item does not fit it is clipped (flagged `clipped`) rather than dropped,
    so a section is never empty merely because the budget is small."""
    kept: list[dict] = []
    used = 0
    for item in items:
        cost = _cost(item["text"])
        if used + cost > budget_tokens:
            if not kept:
                clipped = dict(item)
                clipped["text"] = item["text"][: max(budget_tokens, 1) * 4]
                clipped["clipped"] = True
                kept.append(clipped)
            return kept, True
        entry = dict(item)
        entry.setdefault("clipped", False)
        kept.append(entry)
        used += cost
    return kept, False


def _walk_links(
    store: VectorStore,
    starts: list[tuple[str, int, list[dict], str]],
    seed: Optional[str],
) -> dict[str, tuple[int, list[dict], str]]:
    """BFS over LINK_EDGE_TYPES (both directions) up to _MAX_HOPS.
    `starts` = (node_id, hops, path, reason) entries to enqueue. Returns
    {node_id: (hops, path, reason)} excluding `seed`."""
    found: dict[str, tuple[int, list[dict], str]] = {}
    visited = {seed} if seed else set()
    queue: deque = deque()
    if seed:
        queue.append(seed)
    for node_id, hops, path, reason in starts:
        if node_id in visited:
            continue
        visited.add(node_id)
        found[node_id] = (hops, path, reason)
        queue.append(node_id)

    while queue:
        node_id = queue.popleft()
        hops = 0 if node_id == seed else found[node_id][0]
        if hops >= _MAX_HOPS:
            continue
        base_path = [] if node_id == seed else found[node_id][1]
        for edge in store.graph.get_edges(node_id, "both", LINK_EDGE_TYPES):
            outgoing = edge["source_id"] == node_id
            other = edge["target_id"] if outgoing else edge["source_id"]
            if other in visited:
                continue
            visited.add(other)
            direction = "out" if outgoing else "in"
            step = {"edge_type": edge["edge_type"], "direction": direction, "node": other}
            found[other] = (
                hops + 1,
                base_path + [step],
                _RELATION_LABELS.get((edge["edge_type"], direction), edge["edge_type"]),
            )
            queue.append(other)
    return found


def _tag_set(record: dict) -> set[str]:
    raw = record.get("metadata", {}).get("tags", "") or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def _hybrid_candidates(text: str, k: int, store: VectorStore) -> list[dict]:
    """Vector + BM25 RRF-merged segment candidates, best first. Records from
    the vector side carry a cosine `score`; BM25-only hits carry `score: None`."""
    # The pool is deliberately large: `peers` and `normative` are filtered out
    # of it by tag, so a small pool lets a big requirements corpus push every
    # legal/standard segment out and report a false "no normative match" gap.
    candidate_k = max(k * _CANDIDATE_POOL_PER_K, _MIN_CANDIDATE_POOL)
    vector_results = store.query(text, k=candidate_k)
    bm25_results = [
        (uid, score) for uid, score in store.bm25.query(text, k=candidate_k) if score > 0.0
    ]
    if not vector_results and not bm25_results:
        return []
    merged = _rrf_merge(vector_results, bm25_results, top_k=candidate_k)
    by_id = {r["id"]: r for r in vector_results}
    fetched_by_id = store.get_many([uid for uid, _ in merged if uid not in by_id])
    candidates: list[dict] = []
    for uid, _ in merged:
        record = by_id.get(uid)
        if record is None:
            fetched = fetched_by_id.get(uid)
            if fetched is None:
                continue
            record = {**fetched, "score": None}
        candidates.append(record)
    return candidates


def _peer_items(
    store: VectorStore,
    candidates: list[dict],
    exclude_docs: set[str],
    level: str,
    domain_tags: list[str],
) -> list[dict]:
    """One best segment per requirement-like document, same level / any
    shared domain tag, never a normative document or an excluded one."""
    level_tag = f"level:{level}" if level else ""
    wanted = set(domain_tags)
    normative_tags = {f"doctype:{d}" for d in NORMATIVE_DOCTYPES}
    seen: set[str] = set()
    items: list[dict] = []
    for record in candidates:
        meta = record.get("metadata", {})
        if meta.get("type") != "segment":
            continue
        document_id = meta.get("doc_id")
        if not document_id or document_id in exclude_docs or document_id in seen:
            continue
        tags = _tag_set(record)
        if tags & normative_tags:
            continue
        if level_tag and level_tag not in tags:
            continue
        if wanted and not (wanted & tags):
            continue
        node = store.graph.get_node(document_id)
        if node is None or _placeholder(node):
            continue
        seen.add(document_id)
        score = record.get("score")
        near_duplicate = score is not None and score >= _NEAR_DUPLICATE_THRESHOLD
        items.append({
            "id": document_id,
            "doc_id": document_id,
            "segment_id": record["id"],
            "doc_name": node["name"],
            "text": record["content"],
            "score": score,
            "reason": "near duplicate" if near_duplicate else "similar requirement",
            "near_duplicate": near_duplicate,
            "hops": None,
            "path": [],
        })
    return items


def _normative_items(
    store: VectorStore,
    candidates: list[dict],
    cites: list[dict],
    normative_tags: list[str],
) -> list[dict]:
    """Explicitly cited documents first (best-matching segments, falling back
    to the document's first segment), then semantic matches from segments
    tagged doctype:legal / doctype:standard, optionally scoped by tags."""
    items: list[dict] = []
    seen_segments: set[str] = set()
    cited_docs: set[str] = set()

    for cite in cites:
        target_id = cite["target_id"]
        node = store.graph.get_node(target_id)
        if node is None or _placeholder(node) or target_id in cited_docs:
            continue
        cited_docs.add(target_id)
        own = [
            r for r in candidates
            if r.get("metadata", {}).get("doc_id") == target_id
        ][:2]
        if not own:
            first = store.get_segment_by_ordinal(target_id, 0)
            own = [{**first, "score": None}] if first else []
        for record in own:
            seen_segments.add(record["id"])
            items.append({
                "id": target_id,
                "doc_id": target_id,
                "segment_id": record["id"],
                "doc_name": node["name"],
                "text": record["content"],
                "score": record.get("score"),
                "reason": "explicit citation",
                "clause": cite.get("clause"),
                "hops": None,
                "path": [],
            })

    normative_markers = {f"doctype:{d}" for d in NORMATIVE_DOCTYPES}
    wanted = set(normative_tags)
    for record in candidates:
        meta = record.get("metadata", {})
        if meta.get("type") != "segment" or record["id"] in seen_segments:
            continue
        tags = _tag_set(record)
        kinds = [t.split(":", 1)[1] for t in tags & normative_markers]
        if not kinds:
            continue
        if wanted and not (wanted & tags):
            continue
        document_id = meta.get("doc_id")
        node = store.graph.get_node(document_id) if document_id else None
        if node is None or _placeholder(node):
            continue
        items.append({
            "id": document_id,
            "doc_id": document_id,
            "segment_id": record["id"],
            "doc_name": node["name"],
            "text": record["content"],
            "score": record.get("score"),
            "reason": f"{kinds[0]} match",
            "clause": None,
            "source_kind": kinds[0],
            "hops": None,
            "path": [],
        })
    return items


def requirement_context(
    store: VectorStore,
    *,
    text: str = "",
    doc_id: str = "",
    system: str = "",
    native_id: str = "",
    level: str = "",
    domain_tags: Optional[list[str]] = None,
    normative_tags: Optional[list[str]] = None,
    declared_links: Optional[list[dict]] = None,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    k_per_section: int = _DEFAULT_K,
) -> dict:
    """Sectioned, exact-text evidence for judging a requirement. Input is a
    stored item (`doc_id`, or `system` + `native_id`) and/or a draft `text`.
    No LLM is called; SMOS returns evidence and plain coverage facts only."""
    graph = store.graph
    stored_id = doc_id or (derive_doc_id(system, native_id) if system and native_id else "")
    tags = list(domain_tags or [])

    if stored_id:
        node = graph.get_node(stored_id)
        if node is None or node["type"] != "document" or _placeholder(node):
            return {"error": f"no ingested item for doc_id {stored_id}"}
        if not text:
            text = _doc_text(store, stored_id)
        if not tags:
            tags = list(node["metadata"].get("domain_tags") or [])
        if not system:
            system = node["metadata"].get("system", "") or ""
    elif not text.strip():
        return {"error": "provide text, doc_id, or system + native_id"}

    if not level:
        level = next((t.split(":", 1)[1] for t in tags if t.startswith("level:")), "")

    unresolved: list[str] = []
    starts: list[tuple[str, int, list[dict], str]] = []
    declared_cites: list[dict] = []
    has_upstream = False
    for link in declared_links or []:
        target_system = link.get("target_system") or system
        target_native = link.get("target_native_id", "")
        edge_type = link.get("edge_type", "")
        if not target_system or not target_native:
            unresolved.append(target_native)
            continue
        target_id = derive_doc_id(target_system, target_native)
        target_node = graph.get_node(target_id)
        if target_node is None or _placeholder(target_node):
            unresolved.append(target_native)
            continue
        if edge_type == "depends_on":
            has_upstream = True
        if edge_type == "cites":
            declared_cites.append({"target_id": target_id, "clause": link.get("clause")})
        elif edge_type in LINK_EDGE_TYPES:
            starts.append((
                target_id, 1,
                [{"edge_type": edge_type, "direction": "declared", "node": target_id}],
                f"declared {edge_type}",
            ))

    has_downstream = False
    stored_cites: list[dict] = []
    if stored_id:
        if graph.get_edges(stored_id, "out", ["depends_on"]):
            has_upstream = True
        has_downstream = bool(graph.get_edges(stored_id, "in", ["depends_on"]))
        for edge in graph.get_edges(stored_id, "out", ["cites"]):
            stored_cites.append({
                "target_id": edge["target_id"],
                "clause": edge["metadata"].get("clause"),
            })

    found = _walk_links(store, starts, stored_id or None)

    linked_items: list[dict] = []
    for node_id, (hops, path, reason) in found.items():
        node = graph.get_node(node_id)
        if node is None or node["type"] != "document":
            continue
        if _placeholder(node):
            native = node["metadata"].get("native_id") or node["name"]
            if native not in unresolved:
                unresolved.append(native)
            continue
        linked_items.append({
            "id": node_id,
            "doc_id": node_id,
            "doc_name": node["name"],
            "text": _doc_text(store, node_id),
            "score": None,
            "reason": reason,
            "hops": hops,
            "path": path,
        })
    linked_items.sort(key=lambda i: (
        i["hops"], _EDGE_ORDER.get(i["path"][-1]["edge_type"], 99), i["doc_name"],
    ))

    # an unresolved outgoing link on the stored item itself also counts
    if stored_id:
        for edge in graph.get_edges(stored_id, "out", LINK_EDGE_TYPES + ["cites"]):
            target = graph.get_node(edge["target_id"])
            if _placeholder(target):
                native = target["metadata"].get("native_id") or target["name"]
                if native not in unresolved:
                    unresolved.append(native)

    section_budget = max(token_budget // len(_SECTIONS), 1)
    k = max(k_per_section, 1)
    linked, linked_truncated = _fit(linked_items[:k], section_budget)

    candidates = _hybrid_candidates(text, k, store)
    exclude_docs = {i["doc_id"] for i in linked_items}
    if stored_id:
        exclude_docs.add(stored_id)
    # `level:` tags select the level; only the remaining tags count as domain overlap
    peer_domain_tags = [t for t in tags if not t.startswith("level:")]
    peer_items = _peer_items(store, candidates, exclude_docs, level, peer_domain_tags)
    peers, peers_truncated = _fit(peer_items[:k], section_budget)

    normative_all = _normative_items(
        store, candidates, stored_cites + declared_cites, list(normative_tags or [])
    )
    normative, normative_truncated = _fit(normative_all[:k], section_budget)

    by_source = {d: 0 for d in NORMATIVE_DOCTYPES}
    for item in normative_all:
        kind = item.get("source_kind")
        if kind is None:
            node = graph.get_node(item["doc_id"])
            kind = (node["metadata"].get("doc_type") if node else None) or ""
        if kind in by_source:
            by_source[kind] += 1

    return {
        "query": {
            "doc_id": stored_id or None,
            "text": text,
            "level": level,
            "domain_tags": tags,
        },
        "linked": linked,
        "peers": peers,
        "normative": normative,
        "coverage": {
            "has_upstream": has_upstream,
            "has_downstream": has_downstream,
            "near_duplicates": sum(1 for p in peer_items if p["near_duplicate"]),
            "normative_by_source": by_source,
            "unresolved_links": unresolved,
        },
        "truncated": {
            "linked": linked_truncated,
            "peers": peers_truncated,
            "normative": normative_truncated,
        },
    }


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def requirement_context_from_strings(
    store: VectorStore,
    *,
    text: str = "",
    doc_id: str = "",
    system: str = "",
    native_id: str = "",
    level: str = "",
    domain_tags: str = "",
    normative_tags: str = "",
    declared_links_json: str = "",
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    k_per_section: int = _DEFAULT_K,
) -> dict:
    """String-typed front door for the MCP layer: tags are comma-separated,
    declared links a JSON list of {target_native_id, edge_type[, target_system, clause]}."""
    links: Optional[list[dict]] = None
    if declared_links_json.strip():
        try:
            parsed = json.loads(declared_links_json)
        except json.JSONDecodeError as exc:
            return {"error": f"declared_links_json is not valid JSON: {exc}"}
        if not isinstance(parsed, list) or not all(isinstance(x, dict) for x in parsed):
            return {"error": "declared_links_json must be a JSON list of objects"}
        links = parsed
    return requirement_context(
        store,
        text=text,
        doc_id=doc_id,
        system=system,
        native_id=native_id,
        level=level,
        domain_tags=_split_csv(domain_tags),
        normative_tags=_split_csv(normative_tags),
        declared_links=links,
        token_budget=token_budget,
        k_per_section=k_per_section,
    )

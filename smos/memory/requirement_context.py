from __future__ import annotations

from collections import deque
from typing import Optional

from smos.ingest.contract import derive_doc_id
from smos.memory.exact_retrieval import reconstruct
from smos.memory.vector_store import VectorStore

LINK_EDGE_TYPES = ["depends_on", "parent_of", "member_of"]
NORMATIVE_DOCTYPES = ("legal", "standard")
_MAX_HOPS = 2
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

    peers: list[dict] = []
    normative: list[dict] = []
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
            "near_duplicates": 0,
            "normative_by_source": {d: 0 for d in NORMATIVE_DOCTYPES},
            "unresolved_links": unresolved,
        },
        "truncated": {"linked": linked_truncated, "peers": False, "normative": False},
    }

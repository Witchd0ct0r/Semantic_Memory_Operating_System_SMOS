from __future__ import annotations

from datetime import datetime

from smos.compression.context_builder import build_context
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

_MIN_INPUT_CHARS = 10
_INVALID_INPUT_RESPONSE: dict = {"status": "skipped", "reason": "invalid_input"}


def _validate_text(text: str) -> bool:
    return bool(text) and len(text.strip()) >= _MIN_INPUT_CHARS


def semantic_store(text: str, type: str, store: VectorStore) -> str:
    if not _validate_text(text):
        return ""
    memory = MemoryObject(
        type=type,
        content=text,
        timestamp=datetime.utcnow(),
    )
    return store.store(memory)


def semantic_query(
    query: str,
    k: int,
    store: VectorStore,
    tags: list[str] | None = None,
) -> dict:
    if not _validate_text(query):
        return _INVALID_INPUT_RESPONSE
    result = build_context(query, k, store, tags=tags)
    store.bump_recall(result.sources)
    return result.model_dump()


def semantic_write(
    type: str,
    content: str,
    store: VectorStore,
    tags: list[str] | None = None,
) -> str:
    if not _validate_text(content):
        return ""
    memory = MemoryObject(
        type=type,
        content=content,
        timestamp=datetime.utcnow(),
        tags=tags or [],
    )
    return store.store(memory)


def graph_query(term: str, store: VectorStore, max_hops: int = 2) -> dict:
    """Traverse the memory graph starting from an entity name or exact node id.

    `term` is deliberately not passed through `_validate_text` (its 10-char
    minimum is tuned for prose content, not short lookup terms like "JWT").
    """
    term = term.strip()
    if not term:
        return _INVALID_INPUT_RESPONSE
    start_id = term if store.graph.get_node(term) else f"entity:{term}"
    nodes = store.graph.traverse(start_id, max_hops=max_hops)
    nodes.sort(key=lambda n: n["hop"])
    memory_ids = [n["id"] for n in nodes if n["type"] == "memory"]
    store.bump_recall(memory_ids)
    return {"term": term, "start_id": start_id, "nodes": nodes, "count": len(nodes)}

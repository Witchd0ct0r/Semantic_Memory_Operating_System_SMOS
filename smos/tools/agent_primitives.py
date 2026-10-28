from __future__ import annotations

from datetime import datetime

from smos.ingest.adapters.registry import all_edge_types
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

_MIN_INPUT_CHARS = 10


def _validate_text(text: str) -> bool:
    return bool(text) and len(text.strip()) >= _MIN_INPUT_CHARS


def remember(content: str, type: str, store: VectorStore) -> str:
    if not _validate_text(content):
        return ""
    memory = MemoryObject(type=type, content=content, timestamp=datetime.utcnow())
    return store.store(memory)


def forget(memory_id: str, store: VectorStore) -> bool:
    return store.delete(memory_id)


def learn(content: str, store: VectorStore) -> str:
    if not _validate_text(content):
        return ""
    memory = MemoryObject(
        type="semantic", content=content, timestamp=datetime.utcnow(), confidence=1.0
    )
    return store.store(memory)


def link(id_a: str, id_b: str, edge_type: str, store: VectorStore) -> dict:
    if edge_type not in all_edge_types():
        return {"success": False, "error": f"invalid edge_type: {edge_type}"}
    if store.graph.get_node(id_a) is None or store.graph.get_node(id_b) is None:
        return {"success": False, "error": "one or both ids do not exist as graph nodes"}
    store.graph.add_edge(id_a, id_b, edge_type)
    return {"success": True, "error": None}


def _store_derived(kind: str, text: str, source_ids: list[str], store: VectorStore) -> str:
    """Store caller-written text as a memory derived from existing memories.
    SMOS never writes the text; it validates, stores and links it."""
    if not _validate_text(text) or not source_ids:
        return ""
    if any(store.get_by_uuid(i) is None for i in source_ids):
        return ""
    memory = MemoryObject(
        type=kind,
        content=text,
        timestamp=datetime.utcnow(),
        source_ids=list(source_ids),
    )
    new_id = store.store(memory)
    for source_id in memory.source_ids:
        store.graph.add_edge(new_id, source_id, "generated_from")
    return new_id


def compress(text: str, source_ids: list[str], store: VectorStore) -> str:
    return _store_derived("compressed", text, source_ids, store)


def reflect(text: str, source_ids: list[str], store: VectorStore) -> str:
    return _store_derived("reflection", text, source_ids, store)

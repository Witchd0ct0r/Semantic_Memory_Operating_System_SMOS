from __future__ import annotations

from datetime import datetime

from smos.llm.summarizer import compress_memories_full
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_semantic_snapshot_repo

_MIN_INPUT_CHARS = 10
_VALID_EDGE_TYPES = {
    "references", "depends_on", "related_to",
    "implements", "contains", "evolves_into", "generated_from",
}


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
    if edge_type not in _VALID_EDGE_TYPES:
        return {"success": False, "error": f"invalid edge_type: {edge_type}"}
    if store.graph.get_node(id_a) is None or store.graph.get_node(id_b) is None:
        return {"success": False, "error": "one or both ids do not exist as graph nodes"}
    store.graph.add_edge(id_a, id_b, edge_type)
    return {"success": True, "error": None}


def snapshot(project_root: str, store: VectorStore) -> dict:
    return do_semantic_snapshot_repo(project_root, store)


_COMPRESS_MERGE_QUERY = "Merge these memory fragments into one dense, accurate summary."


def compress(ids: list[str], store: VectorStore) -> str:
    if not ids:
        return ""
    sources = [s for s in (store.get_by_uuid(i) for i in ids) if s is not None]
    if not sources:
        return ""

    memories_for_compression = [
        {"id": s["id"], "content": s["content"], "distance": 1.0} for s in sources
    ]
    summary, confidence, _mode = compress_memories_full(memories_for_compression, _COMPRESS_MERGE_QUERY)

    memory = MemoryObject(
        type="compressed",
        content=summary,
        timestamp=datetime.utcnow(),
        source_ids=[s["id"] for s in sources],
        confidence=confidence,
    )
    new_id = store.store(memory)
    for source_id in memory.source_ids:
        store.graph.add_edge(new_id, source_id, "generated_from")
    return new_id

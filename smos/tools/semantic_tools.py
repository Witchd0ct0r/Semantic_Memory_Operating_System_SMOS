from __future__ import annotations

from datetime import datetime

from smos.compression.context_builder import build_compressed_context
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
    result = build_compressed_context(query, k, store, tags=tags)
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

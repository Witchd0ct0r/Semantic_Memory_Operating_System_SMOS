from __future__ import annotations

from smos.compression.context_builder import build_compressed_context
from smos.memory.vector_store import VectorStore

_VALID_STRATEGIES = {"hybrid", "temporal", "chain", "repo"}


def recall(
    query: str,
    strategy: str,
    k: int,
    since: str,
    until: str,
    seed_id: str,
    component: str,
    store: VectorStore,
) -> dict:
    if strategy not in _VALID_STRATEGIES:
        return {"error": f"unknown strategy: {strategy}"}
    result = build_compressed_context(query, k, store).model_dump()
    store.bump_recall(result.get("sources", []))
    return result

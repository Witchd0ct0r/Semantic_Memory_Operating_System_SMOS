from __future__ import annotations

from typing import Optional

from smos.ingest.adapters.base import DomainAdapter

_CORE_EDGE_TYPES: frozenset[str] = frozenset({
    "references", "depends_on", "related_to",
    "implements", "contains", "evolves_into", "generated_from",
})

_adapters: dict[str, DomainAdapter] = {}


def register_adapter(adapter: DomainAdapter) -> None:
    _adapters[adapter.name] = adapter


def get_adapter(name: str) -> Optional[DomainAdapter]:
    return _adapters.get(name)


def all_edge_types() -> frozenset[str]:
    """Core Phase 2/3 edge vocabulary, unioned with every registered
    adapter's domain-specific edge types (Pillar F traceability edges)."""
    types = set(_CORE_EDGE_TYPES)
    for adapter in _adapters.values():
        types |= set(adapter.edge_types)
    return frozenset(types)


def reset_registry() -> None:
    """Test-only: clear registered adapters between test cases."""
    _adapters.clear()

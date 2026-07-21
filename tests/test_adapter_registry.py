from __future__ import annotations

import pytest

from smos.ingest.adapters.registry import (
    all_edge_types,
    get_adapter,
    register_adapter,
    reset_registry,
)


class _FakeAdapter:
    name = "fake"
    edge_types = frozenset({"satisfies", "verified_by"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self):
        return None


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_all_edge_types_includes_core_vocabulary_with_no_adapters_registered():
    types = all_edge_types()
    assert "references" in types
    assert "depends_on" in types
    assert "contains" in types


def test_register_adapter_extends_edge_vocabulary():
    register_adapter(_FakeAdapter())
    types = all_edge_types()
    assert "satisfies" in types
    assert "verified_by" in types
    assert "references" in types


def test_get_adapter_returns_registered_instance():
    adapter = _FakeAdapter()
    register_adapter(adapter)
    assert get_adapter("fake") is adapter


def test_get_adapter_returns_none_for_unknown_name():
    assert get_adapter("does-not-exist") is None

from __future__ import annotations

import pytest

from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.adapters.registry import all_edge_types, get_adapter, register_adapter, reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_structural_boundaries_splits_on_provisions():
    adapter = LegalAdapter()
    text = "Section 1 requires retention.\nArticle 2 restricts transfer.\nRule 3-A.1 sets a deadline.\n"
    spans = adapter.structural_boundaries(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].startswith("Section 1")
    assert text[spans[1][0]:spans[1][1]].startswith("Article 2")
    assert text[spans[2][0]:spans[2][1]].startswith("Rule 3-A.1")


def test_structural_boundaries_recognizes_section_symbol_marker():
    adapter = LegalAdapter()
    text = "§ 12 Some provision text.\n§ 13 Another provision.\n"
    spans = adapter.structural_boundaries(text)
    assert len(spans) == 2
    assert text[spans[0][0]:spans[0][1]].startswith("§ 12")
    assert text[spans[1][0]:spans[1][1]].startswith("§ 13")


def test_structural_boundaries_empty_for_no_provisions():
    adapter = LegalAdapter()
    assert adapter.structural_boundaries("Just plain prose with no provision markers.") == []


def test_legal_adapter_declares_references_edge_type():
    assert LegalAdapter.edge_types == frozenset({"references"})


def test_legal_adapter_name():
    assert LegalAdapter().name == "legal"


def test_llm_boundary_prompt_is_none():
    assert LegalAdapter().llm_boundary_prompt() is None


def test_registering_legal_adapter_does_not_change_core_vocabulary():
    register_adapter(LegalAdapter())
    assert all_edge_types() == {
        "references", "depends_on", "related_to",
        "implements", "contains", "evolves_into", "generated_from",
    }


def test_get_adapter_finds_explicitly_registered_legal_adapter():
    register_adapter(LegalAdapter())
    adapter = get_adapter("legal")
    assert adapter is not None
    assert adapter.name == "legal"

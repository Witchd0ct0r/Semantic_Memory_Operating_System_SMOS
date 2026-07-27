from __future__ import annotations

import pytest

from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.adapters.registry import all_edge_types, get_adapter, register_adapter, reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_structural_boundaries_splits_on_headings():
    adapter = MarkdownAdapter()
    text = "# Title\nIntro text.\n## Section A\nBody A.\n## Section B\nBody B.\n"
    spans = adapter.structural_boundaries(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].startswith("# Title")
    assert text[spans[1][0]:spans[1][1]].startswith("## Section A")
    assert text[spans[2][0]:spans[2][1]].startswith("## Section B")


def test_structural_boundaries_empty_for_no_headings():
    adapter = MarkdownAdapter()
    assert adapter.structural_boundaries("Just plain text, no headings.") == []


def test_extract_references_emits_self_topic_triple_when_heading_present():
    adapter = MarkdownAdapter()
    assert adapter.extract_references("# Title\nSome text with `no refs`.") == [
        ("Title", "Title", "references")
    ]


def test_extract_references_returns_empty_without_heading():
    adapter = MarkdownAdapter()
    assert adapter.extract_references("Just plain text, no headings, no links.") == []


def test_extract_references_captures_anchor_link_target():
    adapter = MarkdownAdapter()
    text = "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be submitted.\n"
    triples = adapter.extract_references(text)
    assert ("Stress Test", "Stress Test", "references") in triples
    assert ("Stress Test", "definitions", "references") in triples


def test_extract_references_captures_wikilink_target():
    adapter = MarkdownAdapter()
    text = "## Section A\nSee [[Covered Institution]] for the definition.\n"
    triples = adapter.extract_references(text)
    assert ("Section A", "Covered Institution", "references") in triples


def test_extract_references_skips_external_urls():
    adapter = MarkdownAdapter()
    text = "## External\nSee [the site](https://example.com) for more.\n"
    assert adapter.extract_references(text) == [("External", "External", "references")]


def test_markdown_adapter_declares_references_edge_type():
    assert MarkdownAdapter.edge_types == frozenset({"references"})


def test_llm_boundary_prompt_is_none():
    assert MarkdownAdapter().llm_boundary_prompt() is None


def test_registering_markdown_adapter_does_not_change_core_vocabulary():
    register_adapter(MarkdownAdapter())
    assert all_edge_types() == {
        "references", "depends_on", "related_to",
        "implements", "contains", "evolves_into", "generated_from",
    }


def test_get_adapter_finds_explicitly_registered_markdown_adapter():
    register_adapter(MarkdownAdapter())
    adapter = get_adapter("markdown")
    assert adapter is not None
    assert adapter.name == "markdown"

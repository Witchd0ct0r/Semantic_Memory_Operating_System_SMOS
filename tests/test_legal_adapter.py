from __future__ import annotations

from pathlib import Path

import pytest

from smos.compression.context_builder import build_context
from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.adapters.registry import all_edge_types, get_adapter, register_adapter, reset_registry
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


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


def test_extract_references_returns_empty_without_matches():
    adapter = LegalAdapter()
    assert adapter.extract_references("plain lowercase text with nothing capitalized.") == []


def test_extract_references_citation_cross_reference():
    adapter = LegalAdapter()
    text = "Section 3.1 permits disclosure. See also Section 5.2 for exceptions."
    triples = adapter.extract_references(text)
    assert ("Section 5.2", "Section 3.1", "references") in triples


def test_extract_references_defining_pattern_emits_self_topic():
    adapter = LegalAdapter()
    text = "Covered Institution means any entity that holds more than ten billion dollars in assets under management."
    triples = adapter.extract_references(text)
    assert ("Covered Institution", "Covered Institution", "references") in triples


def test_extract_references_defining_pattern_handles_quoted_term():
    adapter = LegalAdapter()
    text = '"Qualified Custodian" means a bank or trust company that meets the segregation-of-assets standard.'
    triples = adapter.extract_references(text)
    assert any(target == "Qualified Custodian" for _source, target, _edge in triples)


def test_extract_references_title_case_phrase_without_definition():
    adapter = LegalAdapter()
    text = "Annual stress-test results for a Covered Institution must be submitted no later than March 31."
    triples = adapter.extract_references(text)
    assert any(target == "Covered Institution" for _source, target, _edge in triples)


def test_extract_references_only_uses_references_edge_type():
    adapter = LegalAdapter()
    text = "Section 1 permits disclosure. Section 2 restricts it. Covered Institution means a large entity."
    triples = adapter.extract_references(text)
    assert all(edge_type == "references" for _s, _t, edge_type in triples)


def test_legal_cross_reference_graph_rescue_end_to_end(store: VectorStore) -> None:
    text = (
        "Section 1. Definitions.\n"
        "Covered Institution means any entity that holds more than ten billion "
        "dollars in assets under management.\n\n"
        "Section 2. Stress Testing.\n"
        "Annual stress-test results for a Covered Institution must be submitted "
        "no later than March 31.\n"
    )
    payload = markdown_to_payload(text, system="legal-corpus", native_id="doc-legal-e2e")

    result = do_ingest_document_schema(payload, store, adapter=LegalAdapter())
    assert result["status"] == "success"
    assert result["reference_count"] >= 1

    target_id = next(
        sid for sid in result["segment_ids"]
        if "Annual stress-test results" in store.get_by_uuid(sid)["content"]
    )

    context = build_context(
        "asset threshold definition ten billion dollars", k=1, store=store
    )
    assert target_id in context.sources


# --- Track C: document-scoped citation concept ids -------------------------

_CITING_TEXT = (
    "Article 1. Scope.\n"
    "Operators must comply with Article 3 at all times.\n\n"
    "Article 3. Duties.\n"
    "Operators shall keep records of every Data Subject request.\n"
)


def _concept_ids(store: VectorStore, segment_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for sid in segment_ids:
        for node in store.graph.traverse(sid, max_hops=1, edge_types=["references"]):
            if node["id"].startswith("concept:"):
                found.add(node["id"])
    return found


def test_is_document_local_true_for_provision_citations():
    adapter = LegalAdapter()
    assert adapter.is_document_local("Article 3") is True
    assert adapter.is_document_local("Section 5.2") is True
    assert adapter.is_document_local("§ 7") is True


def test_is_document_local_false_for_defined_terms():
    adapter = LegalAdapter()
    assert adapter.is_document_local("Covered Institution") is False


def test_same_article_citation_in_two_documents_gets_distinct_concept_nodes(store: VectorStore) -> None:
    payload_a = markdown_to_payload(_CITING_TEXT, system="eurlex", native_id="reg-a")
    payload_b = markdown_to_payload(_CITING_TEXT, system="eurlex", native_id="reg-b")
    result_a = do_ingest_document_schema(payload_a, store, adapter=LegalAdapter())
    result_b = do_ingest_document_schema(payload_b, store, adapter=LegalAdapter())
    assert result_a["status"] == "success" and result_b["status"] == "success"
    assert result_a["doc_id"] != result_b["doc_id"]

    concepts_a = _concept_ids(store, result_a["segment_ids"])
    concepts_b = _concept_ids(store, result_b["segment_ids"])

    provision_a = {c for c in concepts_a if "article" in c}
    provision_b = {c for c in concepts_b if "article" in c}
    assert provision_a, "expected at least one provision-citation concept node"
    assert provision_a.isdisjoint(provision_b)
    assert all(c.startswith(f"concept:{result_a['doc_id']}:") for c in provision_a)
    assert all(c.startswith(f"concept:{result_b['doc_id']}:") for c in provision_b)
    assert store.graph.get_node("concept:article 3") is None


def test_defined_term_concept_stays_shared_across_documents(store: VectorStore) -> None:
    payload_a = markdown_to_payload(_CITING_TEXT, system="eurlex", native_id="reg-a")
    payload_b = markdown_to_payload(_CITING_TEXT, system="eurlex", native_id="reg-b")
    result_a = do_ingest_document_schema(payload_a, store, adapter=LegalAdapter())
    result_b = do_ingest_document_schema(payload_b, store, adapter=LegalAdapter())

    term_a = {c for c in _concept_ids(store, result_a["segment_ids"]) if "data subject" in c}
    term_b = {c for c in _concept_ids(store, result_b["segment_ids"]) if "data subject" in c}
    assert term_a and term_a == term_b
    assert term_a == {"concept:data subject"}

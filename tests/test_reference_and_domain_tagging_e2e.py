from __future__ import annotations

from pathlib import Path

import pytest

from smos.compression.context_builder import build_compressed_context
from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_markdown_cross_reference_graph_rescue_end_to_end(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten "
        "billion dollars in assets under management.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be "
        "submitted no later than March 31.\n"
    )
    payload = markdown_to_payload(text, system="wiki", native_id="doc-e2e-refs")

    result = do_ingest_document_schema(payload, store, adapter=MarkdownAdapter())
    assert result["status"] == "success"
    assert result["reference_count"] >= 1

    stress_id = next(
        sid for sid in result["segment_ids"]
        if "Annual results" in store.get_by_uuid(sid)["content"]
    )

    context = build_compressed_context(
        "asset threshold definition ten billion dollars", k=1, store=store
    )
    assert stress_id in context.sources


def test_domain_tags_and_references_compose_on_the_same_ingest(store: VectorStore) -> None:
    text = (
        "# Title\n"
        "## Definitions\nCovered Institution means any entity that holds more than ten "
        "billion dollars in assets under management.\n"
        "## Stress Test\nAnnual results for a [Covered Institution](#definitions) must be "
        "submitted no later than March 31.\n"
    )
    payload = markdown_to_payload(text, system="wiki", native_id="doc-e2e-domains")
    payload.domain_tags = ["financial-regulation"]

    result = do_ingest_document_schema(payload, store, adapter=MarkdownAdapter())

    assert result["status"] == "success"
    assert result["reference_count"] >= 1
    doc_node = store.graph.get_node(result["doc_id"])
    assert doc_node["metadata"]["domain_tags"] == ["financial-regulation"]

    scoped = store.query_domain(
        "stress test submission deadline", k=5, domain_tags=["financial-regulation"]
    )
    assert any(r["id"] in result["segment_ids"] for r in scoped)

from __future__ import annotations

from smos.ingest.contract import (
    ContentBlock,
    ExternalHandle,
    IngestionPayload,
    SourceLink,
    content_hash,
    derive_doc_id,
)


def test_ingestion_payload_requires_external_handle_and_blocks():
    payload = IngestionPayload(
        external_handle=ExternalHandle(system="wiki", native_id="doc-1"),
        blocks=[ContentBlock(block_type="paragraph", text="Hello world.")],
    )
    assert payload.schema_version == "1.0"
    assert payload.external_handle.system == "wiki"
    assert payload.blocks[0].text == "Hello world."
    assert payload.source_links == []
    assert payload.domain_tags == []


def test_content_block_allows_optional_heading_level_and_source_anchor():
    block = ContentBlock(
        block_type="heading", text="# Title", heading_level=1, source_anchor="doc-1#h1"
    )
    assert block.heading_level == 1
    assert block.source_anchor == "doc-1#h1"


def test_source_link_carries_edge_type_and_metadata():
    link = SourceLink(target_native_id="req-42", edge_type="satisfies", metadata={"note": "x"})
    assert link.target_native_id == "req-42"
    assert link.metadata == {"note": "x"}


def test_derive_doc_id_is_deterministic_and_keyed_on_system_and_native_id():
    id_a = derive_doc_id("wiki", "doc-1")
    id_b = derive_doc_id("wiki", "doc-1")
    id_c = derive_doc_id("wiki", "doc-2")
    id_d = derive_doc_id("jira", "doc-1")
    assert id_a == id_b
    assert id_a != id_c
    assert id_a != id_d


def test_content_hash_is_deterministic_and_sensitive_to_text():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("world")

from __future__ import annotations

from pathlib import Path

import pytest

from smos.ingest.contract import blocks_to_full_text
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def test_markdown_to_payload_produces_heading_and_paragraph_blocks():
    md = "# Title\nIntro paragraph text here.\n## Section A\nShort body A.\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-1")

    assert payload.external_handle.system == "wiki"
    assert payload.external_handle.native_id == "doc-1"
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "paragraph", "heading", "paragraph"]
    assert payload.blocks[0].heading_level == 1
    assert payload.blocks[2].heading_level == 2


def test_markdown_to_payload_round_trips_byte_exact():
    md = (
        "# Title\nIntro paragraph text here.\n"
        "## Section A\nShort body A.\n"
        "## Section B\nShort body B.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-2")
    full_text, _ = blocks_to_full_text(payload.blocks)
    assert full_text == md


def test_markdown_to_payload_handles_no_headings():
    md = "Just a plain paragraph with no headings at all.\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-3")
    assert len(payload.blocks) == 1
    assert payload.blocks[0].block_type == "paragraph"


def test_end_to_end_ingest_reconstructs_original_markdown(store: VectorStore) -> None:
    md = (
        "# Title\nIntro paragraph text here.\n"
        "## Section A\n" + ("Sentence about topic A. " * 60) + "\n"
        "## Section B\nShort body B.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-roundtrip")
    full_text, _ = blocks_to_full_text(payload.blocks)

    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "success"

    records = sorted(
        (store.get_by_uuid(sid) for sid in result["segment_ids"]),
        key=lambda r: r["metadata"]["ordinal"],
    )
    reconstructed = "".join(
        full_text[r["metadata"]["char_start"] : r["metadata"]["char_end"]] for r in records
    )
    assert reconstructed == md


def test_markdown_to_payload_types_bullet_list_block():
    md = "# Title\n- item one\n- item two\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-list")
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "list"]
    assert payload.blocks[1].text == "\n- item one\n- item two\n"


def test_markdown_to_payload_types_table_block():
    md = "# Title\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    payload = markdown_to_payload(md, system="wiki", native_id="doc-table")
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "table"]
    assert payload.blocks[1].text == "\n| a | b |\n|---|---|\n| 1 | 2 |\n"


def test_markdown_to_payload_multiline_list_item_stays_one_list_block():
    md = (
        "# Title\n"
        "- First item starts here\n"
        "  and wraps onto a continuation line.\n"
        "- Second item.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-wrap")
    types = [b.block_type for b in payload.blocks]
    assert types == ["heading", "list"]


def test_markdown_to_payload_mixed_list_table_paragraph_round_trips_byte_exact():
    md = (
        "# Title\n"
        "Intro paragraph.\n"
        "- item one\n"
        "- item two\n"
        "\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
        "\n"
        "Closing paragraph.\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-mixed")
    full_text, _ = blocks_to_full_text(payload.blocks)
    assert full_text == md
    assert all(b.text.strip() for b in payload.blocks)


def test_end_to_end_ingest_with_lists_and_tables_reconstructs_original_markdown(
    store: VectorStore,
) -> None:
    md = (
        "# Title\n"
        "Intro paragraph.\n"
        "- item one\n"
        "- item two\n"
        "\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
    )
    payload = markdown_to_payload(md, system="wiki", native_id="doc-mixed-ingest")
    full_text, _ = blocks_to_full_text(payload.blocks)

    result = do_ingest_document_schema(payload, store)
    assert result["status"] == "success"

    records = sorted(
        (store.get_by_uuid(sid) for sid in result["segment_ids"]),
        key=lambda r: r["metadata"]["ordinal"],
    )
    reconstructed = "".join(
        full_text[r["metadata"]["char_start"] : r["metadata"]["char_end"]] for r in records
    )
    assert reconstructed == md

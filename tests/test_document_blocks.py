from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload, derive_doc_id
from smos.memory.vector_store import VectorStore
from smos.storage.migrations import STORE_MIGRATIONS, run_migrations
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _payload(blocks: list[tuple[str, str]], native_id: str = "D1") -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="s", native_id=native_id),
        blocks=[ContentBlock(block_type=t, text=x, heading_level=1 if t == "heading" else None) for t, x in blocks],
    )


LONG = "Der Grenzwert gilt für Fahrzeuge der Klasse M1 und N1. " * 25 + "\n"   # > 800 characters, umlauts
BLOCKS = [
    ("heading", "1 Scope\n"),                                   # under 20 characters
    ("paragraph", "This standard applies to widgets used in vehicles of all kinds.\n"),
    ("heading", "2 Limits\n"),                                  # under 20 characters
    ("paragraph", "The maximum permitted deflection shall not exceed two millimetres.\n"),
    ("paragraph", LONG),
]


def test_migration_5_creates_table_on_existing_store() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn, STORE_MIGRATIONS[:4])
    run_migrations(conn, STORE_MIGRATIONS)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(document_blocks)")]
    assert cols == ["doc_id", "ordinal", "char_start", "char_end", "block_type"]
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 5


def test_ingest_stores_blocks_that_tile_the_text(store: VectorStore) -> None:
    result = do_ingest_document_schema(_payload(BLOCKS), store)
    doc_id = result["doc_id"]
    blocks = store.get_blocks(doc_id)
    text = "".join(t for _, t in BLOCKS)
    assert [b["ordinal"] for b in blocks] == list(range(len(BLOCKS)))
    assert blocks[0]["char_start"] == 0 and blocks[-1]["char_end"] == len(text)
    for prev, cur in zip(blocks, blocks[1:]):
        assert cur["char_start"] == prev["char_end"]
    assert "".join(text[b["char_start"]:b["char_end"]] for b in blocks) == text
    assert [b["block_type"] for b in blocks] == [t for t, _ in BLOCKS]


def test_short_block_range_survives_segment_merge(store: VectorStore) -> None:
    doc_id = do_ingest_document_schema(_payload(BLOCKS), store)["doc_id"]
    first = store.get_blocks(doc_id)[0]
    assert (first["char_start"], first["char_end"]) == (0, len("1 Scope\n"))
    segment0 = store.get_segment_by_ordinal(doc_id, 0)
    assert segment0["metadata"]["char_start"] == 0
    assert segment0["metadata"]["char_end"] > first["char_end"]     # the segmenter merged the short block away


def test_block_containing_finds_the_block_for_a_position(store: VectorStore) -> None:
    doc_id = do_ingest_document_schema(_payload(BLOCKS), store)["doc_id"]
    blocks = store.get_blocks(doc_id)
    for b in blocks:
        assert store.block_containing(doc_id, b["char_start"])["ordinal"] == b["ordinal"]
        assert store.block_containing(doc_id, b["char_end"] - 1)["ordinal"] == b["ordinal"]
    assert store.block_containing(doc_id, blocks[-1]["char_end"]) is None
    assert store.block_containing("unknown-doc", 0) is None


def test_reingest_replaces_blocks(store: VectorStore) -> None:
    do_ingest_document_schema(_payload(BLOCKS), store)
    result = do_ingest_document_schema(_payload([("paragraph", "A completely different and much shorter text.\n"),
                                                 ("paragraph", "And a second block of this new document text.\n")]), store)
    blocks = store.get_blocks(result["doc_id"])
    assert len(blocks) == 2 and blocks[1]["char_end"] == len("A completely different and much shorter text.\n"
                                                              "And a second block of this new document text.\n")
    assert store.get_blocks(derive_doc_id("s", "none")) == []

from __future__ import annotations

import sqlite3

from smos.storage.migrations import STORE_MIGRATIONS, run_migrations


def _v3_db_with_rows(rows: list[tuple[str, str]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn, STORE_MIGRATIONS[:3])
    for i, (uuid_, tags) in enumerate(rows):
        conn.execute(
            "INSERT INTO memories (uuid, type, content, timestamp, tags) VALUES (?,?,?,?,?)",
            (uuid_, "segment", f"content {i}", "2026-01-01T00:00:00", tags),
        )
    conn.commit()
    return conn


def test_migration_4_backfills_existing_rows() -> None:
    conn = _v3_db_with_rows([("u1", "a:b,c"), ("u2", ""), ("u3", "c")])
    run_migrations(conn, STORE_MIGRATIONS)
    ids = {u: r for r, u in conn.execute("SELECT row_id, uuid FROM memories")}
    got = set(conn.execute("SELECT row_id, tag FROM memory_tags").fetchall())
    assert got == {(ids["u1"], "a:b"), (ids["u1"], "c"), (ids["u3"], "c")}


def test_delete_trigger_removes_tags() -> None:
    conn = _v3_db_with_rows([("u1", "a,b"), ("u2", "a")])
    run_migrations(conn, STORE_MIGRATIONS)
    conn.execute("DELETE FROM memories WHERE uuid = 'u1'")
    conn.commit()
    rows = conn.execute("SELECT tag FROM memory_tags").fetchall()
    assert rows == [("a",)]


# ─── Task 2: insert helper, TagFilter, eligibility ──────────────────────────

from pathlib import Path

import pytest

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload
from smos.memory.schemas import MemoryObject
from smos.memory.tagfilter import TagFilter
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema


@pytest.fixture
def store(tmp_path: Path) -> VectorStore:
    return VectorStore(persist_path=tmp_path / "data")


def _mem(content: str, tags: list[str], type_: str = "doc") -> MemoryObject:
    return MemoryObject(type=type_, content=content, tags=tags)


def _tag_rows(store: VectorStore) -> set[tuple[str, str]]:
    return set(store._db.execute(
        "SELECT m.uuid, t.tag FROM memory_tags t JOIN memories m ON m.row_id = t.row_id"
    ).fetchall())


def _expected_rows(store: VectorStore) -> set[tuple[str, str]]:
    out = set()
    for uuid_, tags in store._db.execute("SELECT uuid, tags FROM memories").fetchall():
        for tag in tags.split(","):
            if tag:
                out.add((uuid_, tag))
    return out


def test_memory_tags_in_sync_after_store_batch_and_delete(store: VectorStore) -> None:
    single = store.store(_mem("alpha beta gamma delta", ["a", "b"]))
    batch = store.store_batch([_mem("one two three four five", ["a"]), _mem("six seven eight nine ten", ["c:d"])])
    assert _tag_rows(store) == _expected_rows(store) != set()
    store.delete(single)
    assert _tag_rows(store) == _expected_rows(store)
    store.delete_batch(batch)
    assert _tag_rows(store) == _expected_rows(store) == set()


def test_reingest_with_changed_tags_leaves_no_stale_rows(store: VectorStore) -> None:
    def payload(tags: list[str], text: str) -> IngestionPayload:
        return IngestionPayload(
            external_handle=ExternalHandle(system="s", native_id="d1"),
            blocks=[ContentBlock(block_type="paragraph", text=text)],
            domain_tags=tags,
        )
    do_ingest_document_schema(payload(["a"], "First version of the text about lead and cadmium limits.\n"), store)
    assert store.unknown_tags(["a"]) == []
    do_ingest_document_schema(payload(["b"], "Second, different version of the text about noise limits.\n"), store)
    assert store.unknown_tags(["a"]) == ["a"]
    assert store.unknown_tags(["b"]) == []
    assert _tag_rows(store) == _expected_rows(store)


def test_wildcard_characters_match_only_themselves(store: VectorStore) -> None:
    first = store.store(_mem("content with percent tag here", ["norm:MBST%"]))
    store.store(_mem("content with another tag there", ["norm:MBSTX"]))
    store.store(_mem("content with underscore tag now", ["a_b"]))
    store.store(_mem("content with plain letter tag now", ["axb"]))
    got = store.resolve_tag_filter(TagFilter.make(any=["norm:MBST%"]))
    assert got is not None and got.uuids == frozenset({first})
    got = store.resolve_tag_filter(TagFilter.make(any=["a_b"]))
    assert got is not None and len(got.uuids) == 1


def test_resolve_semantics(store: VectorStore) -> None:
    ab = store.store(_mem("both tags are present on this", ["a", "b"]))
    a = store.store(_mem("only the first tag is here now", ["a"]))
    upper = store.store(_mem("an upper case tag lives here", ["Norm:X"]))
    store.store(_mem("no tags at all on this memory", []))

    def uuids(**kw) -> frozenset[str]:
        got = store.resolve_tag_filter(TagFilter.make(**kw))
        assert got is not None
        assert len(got.row_ids) == len(got.uuids)
        return got.uuids

    assert uuids(any=["a", "zzz"]) == {ab, a}
    assert uuids(all=["a", "b"]) == {ab}
    assert uuids(any=["a"], none=["b"]) == {a}
    assert uuids(all=["a"], none=["b"], any=["a"]) == {a}
    assert uuids(any=["norm:x"]) == frozenset()          # case-sensitive
    assert uuids(any=["Norm:X"]) == {upper}
    assert uuids(none=["a"]) - {ab, a} != frozenset()    # none-only keeps untagged memories
    assert store.resolve_tag_filter(TagFilter.make()) is None
    assert store.resolve_tag_filter(TagFilter.make(any=["", "  "])) is None


def test_unknown_tags_preserves_order_and_dedupes(store: VectorStore) -> None:
    store.store(_mem("some tagged memory content", ["known"]))
    assert store.unknown_tags(["zzz", "known", "yyy", "zzz"]) == ["zzz", "yyy"]


def test_tag_counts_prefix_and_paging(store: VectorStore) -> None:
    store.store(_mem("memory number one here", ["norm:A", "topic:x"]))
    store.store(_mem("memory number two here", ["norm:A", "norm:B"]))
    total, rows = store.tag_counts(prefix="norm:")
    assert total == 2 and rows == [{"tag": "norm:A", "count": 2}, {"tag": "norm:B", "count": 1}]
    total, rows = store.tag_counts(limit=1, offset=1)
    assert total == 3 and rows == [{"tag": "norm:B", "count": 1}]

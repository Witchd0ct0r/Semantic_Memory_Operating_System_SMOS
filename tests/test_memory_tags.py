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

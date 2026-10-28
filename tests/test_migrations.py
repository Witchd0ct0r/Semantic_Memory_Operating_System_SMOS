from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from smos.memory.vector_store import VectorStore
from smos.storage.migrations import Migration, SchemaTooNewError, run_migrations


def _create(name: str):
    def apply(conn: sqlite3.Connection) -> None:
        conn.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)")
    return apply


def _boom(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE half_done (id INTEGER)")
    raise RuntimeError("migration exploded")


def _version(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT version FROM schema_version").fetchone()[0]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    yield conn, path
    conn.close()


def test_fresh_db_applies_all_in_order(db) -> None:
    conn, path = db
    migrations: list[Migration] = [(1, "a", _create("a")), (2, "b", _create("b"))]
    assert run_migrations(conn, migrations, path) == 2
    assert {"a", "b", "schema_version"} <= _tables(conn)
    assert _version(conn) == 2


def test_rerun_is_noop(db) -> None:
    conn, path = db
    migrations: list[Migration] = [(1, "a", _create("a"))]
    run_migrations(conn, migrations, path)
    assert run_migrations(conn, migrations, path) == 1  # would raise "table exists" if re-applied


def test_failed_migration_rolls_back_everything_and_keeps_backup(db) -> None:
    conn, path = db
    run_migrations(conn, [(1, "a", _create("a"))], path)
    conn.execute("INSERT INTO a (id) VALUES (7)")
    conn.commit()
    with pytest.raises(RuntimeError, match="exploded"):
        run_migrations(conn, [(1, "a", _create("a")), (2, "b", _create("b")), (3, "bad", _boom)], path)
    assert _version(conn) == 1
    assert "b" not in _tables(conn) and "half_done" not in _tables(conn)
    assert conn.execute("SELECT id FROM a").fetchall() == [(7,)]
    assert (path.parent / f"{path.name}.bak-v1").exists()


def test_newer_schema_refused(db) -> None:
    conn, path = db
    run_migrations(conn, [(1, "a", _create("a")), (2, "b", _create("b"))], path)
    with pytest.raises(SchemaTooNewError):
        run_migrations(conn, [(1, "a", _create("a"))], path)


def test_backup_file_created_before_upgrade(db) -> None:
    conn, path = db
    run_migrations(conn, [(1, "a", _create("a"))], path)
    conn.execute("INSERT INTO a (id) VALUES (1)")
    conn.commit()
    run_migrations(conn, [(1, "a", _create("a")), (2, "b", _create("b"))], path)
    backup = path.parent / f"{path.name}.bak-v1"
    assert backup.exists()
    bconn = sqlite3.connect(str(backup))
    try:
        assert "b" not in _tables(bconn)
        assert bconn.execute("SELECT id FROM a").fetchall() == [(1,)]
    finally:
        bconn.close()


def test_no_backup_on_fresh_install(db) -> None:
    conn, path = db
    run_migrations(conn, [(1, "a", _create("a"))], path)
    assert not list(path.parent.glob("*.bak-*"))


def test_duplicate_or_unordered_versions_rejected(db) -> None:
    conn, path = db
    with pytest.raises(ValueError):
        run_migrations(conn, [(1, "a", _create("a")), (1, "again", _create("c"))], path)


def test_store_baseline_creates_tables(tmp_path: Path) -> None:
    store = VectorStore(persist_path=tmp_path / "data")
    tables = _tables(store._db)
    assert {"memories", "verbatim", "ingest_quarantine", "ingest_checkpoints",
            "summaries", "schema_version"} <= tables
    assert "ingested_files" not in tables
    assert _version(store._db) == 1
    store.close()


def test_store_reopen_is_idempotent(tmp_path: Path) -> None:
    VectorStore(persist_path=tmp_path / "data").close()
    store = VectorStore(persist_path=tmp_path / "data")
    assert _version(store._db) == 1
    store.close()

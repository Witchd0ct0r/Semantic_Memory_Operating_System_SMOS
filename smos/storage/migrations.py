from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable, Optional

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


class SchemaTooNewError(RuntimeError):
    """The database was written by a newer SMOS than the one running."""


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def run_migrations(
    conn: sqlite3.Connection,
    migrations: list[Migration],
    db_path: Optional[Path] = None,
) -> int:
    """Apply pending forward-only migrations; return the resulting version.

    All pending migrations run in ONE transaction, so a failure leaves the
    database exactly as it was (and the pre-upgrade backup copy, taken first
    when the database already had a version, is kept as a safety net).
    """
    versions = [m[0] for m in migrations]
    if versions != sorted(set(versions)):
        raise ValueError("migration versions must be unique and ascending")
    latest = versions[-1] if versions else 0

    conn.commit()
    current = _current_version(conn)
    conn.commit()
    if current > latest:
        raise SchemaTooNewError(
            f"database schema version {current} is newer than this SMOS supports ({latest}); "
            "upgrade SMOS instead of opening this data directory with an older release"
        )
    pending = [m for m in migrations if m[0] > current]
    if not pending:
        return current

    if current > 0 and db_path is not None:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(db_path, db_path.parent / f"{db_path.name}.bak-v{current}")

    conn.execute("BEGIN IMMEDIATE")
    try:
        for version, _name, apply in pending:
            apply(conn)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (pending[-1][0],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return pending[-1][0]


def _baseline(conn: sqlite3.Connection) -> None:
    """Version 1: the full memory-store schema on an empty database."""
    conn.execute("""
        CREATE TABLE memories (
            row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid          TEXT UNIQUE NOT NULL,
            type          TEXT NOT NULL,
            content       TEXT NOT NULL,
            timestamp     TEXT NOT NULL,
            tags          TEXT NOT NULL DEFAULT '',
            tier          TEXT NOT NULL DEFAULT 'hot',
            source_ids    TEXT NOT NULL DEFAULT '',
            confidence    REAL NOT NULL DEFAULT 1.0,
            recall_count  INTEGER NOT NULL DEFAULT 0,
            last_recalled TEXT,
            doc_id        TEXT,
            ordinal       INTEGER,
            char_start    INTEGER,
            char_end      INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE summaries (
            node_id    TEXT PRIMARY KEY,
            summary    TEXT NOT NULL,
            source     TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE verbatim (
            key       TEXT PRIMARY KEY,
            content   TEXT NOT NULL,
            label     TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE ingest_quarantine (
            key            TEXT PRIMARY KEY,
            system         TEXT NOT NULL,
            native_id      TEXT NOT NULL,
            raw_payload    TEXT NOT NULL,
            errors         TEXT NOT NULL,
            quarantined_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE ingest_checkpoints (
            doc_id          TEXT PRIMARY KEY,
            content_hash    TEXT NOT NULL,
            total_leaves    INTEGER NOT NULL,
            next_leaf_index INTEGER NOT NULL DEFAULT 0,
            segment_ids     TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'in_progress',
            updated_at      TEXT NOT NULL
        )
    """)


def _memories_type_index(conn: sqlite3.Connection) -> None:
    """Version 2: makes the dashboard's per-type counts cheap (index-only) on large stores."""
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories (type)")


def _graph_tables(conn: sqlite3.Connection) -> None:
    """Version 3: the graph tables, previously created by GraphStore itself.

    Databases written by earlier builds already have them. Two even older shapes are rebuilt in place:
    a `graph_nodes` CHECK that predates the 'section' type (Phase 5.2; the guard keys off 'section', not
    'document', so a 5.1-era table is not mistaken for migrated) and a `graph_edges` CHECK that restricted
    `edge_type` to a fixed vocabulary (Phase 5 made it open text, validated in application code).
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_nodes'").fetchone()
    if row is not None and row[0] is not None and "'section'" not in row[0]:
        conn.execute("ALTER TABLE graph_nodes RENAME TO graph_nodes_legacy")
        conn.execute("""
        CREATE TABLE graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document','section')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
        conn.execute(
            "INSERT INTO graph_nodes (id, type, name, metadata, created_at) "
            "SELECT id, type, name, metadata, created_at FROM graph_nodes_legacy"
        )
        conn.execute("DROP TABLE graph_nodes_legacy")
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_edges'").fetchone()
    if row is not None and row[0] is not None and "CHECK(edge_type" in row[0]:
        conn.execute("ALTER TABLE graph_edges RENAME TO graph_edges_legacy")
        conn.execute("""
        CREATE TABLE graph_edges (
            id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            source_id  TEXT NOT NULL,
            target_id  TEXT NOT NULL,
            edge_type  TEXT NOT NULL,
            weight     REAL DEFAULT 1.0,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, edge_type)
        )
    """)
        conn.execute(
            "INSERT INTO graph_edges (id, source_id, target_id, edge_type, weight, metadata, created_at) "
            "SELECT id, source_id, target_id, edge_type, weight, metadata, created_at FROM graph_edges_legacy"
        )
        conn.execute("DROP TABLE graph_edges_legacy")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id         TEXT PRIMARY KEY,
            type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document','section')),
            name       TEXT NOT NULL,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS graph_edges (
            id         TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            source_id  TEXT NOT NULL,
            target_id  TEXT NOT NULL,
            edge_type  TEXT NOT NULL,
            weight     REAL DEFAULT 1.0,
            metadata   TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, edge_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes (type)")


STORE_MIGRATIONS: list[Migration] = [
    (1, "baseline", _baseline),
    (2, "memories_type_index", _memories_type_index),
    (3, "graph_tables", _graph_tables),
]

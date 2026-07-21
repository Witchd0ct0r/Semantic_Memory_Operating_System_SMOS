from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional


class GraphStore:
    """Thread-safe node/edge graph layer over a shared SQLite connection.

    Shares the caller's `sqlite3.Connection` (same pattern VectorStore uses
    for `memories`/`verbatim`/`ingested_files`) rather than opening its own
    database file. Every public method holds its own internal lock, mirroring
    BM25Index — callers do not need to hold any outer lock to use GraphStore
    safely on its own, though VectorStore does call it from within its own
    `_lock` anyway (see Task 4).
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._migrate_legacy_graph_nodes_check()
            self._migrate_legacy_graph_edges_check()
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id         TEXT PRIMARY KEY,
                    type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document','section')),
                    name       TEXT NOT NULL,
                    metadata   TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._db.execute("""
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
            self._db.commit()

    def _migrate_legacy_graph_nodes_check(self) -> None:
        """Rebuild graph_nodes if it exists with a CHECK constraint that
        predates the 'section' type (added Phase 5.2). Covers both pre-Phase-5
        DBs (no 'document', no 'section') and 5.1-era DBs (has 'document', not
        'section') — the guard keys off 'section' specifically so a 5.1 DB
        isn't mistaken for already-migrated. SQLite has no ALTER TABLE for
        CHECK constraints, so this copies existing rows into a freshly
        constrained table. Only called from _init_schema, which already holds
        self._lock — no locking here.
        """
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_nodes'"
        ).fetchone()
        if row is None or row[0] is None or "'section'" in row[0]:
            return  # table doesn't exist yet, or already migrated
        self._db.execute("ALTER TABLE graph_nodes RENAME TO graph_nodes_legacy")
        self._db.execute("""
            CREATE TABLE graph_nodes (
                id         TEXT PRIMARY KEY,
                type       TEXT NOT NULL CHECK(type IN ('entity','concept','memory','repo_component','snapshot','agent','document','section')),
                name       TEXT NOT NULL,
                metadata   TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._db.execute(
            "INSERT INTO graph_nodes (id, type, name, metadata, created_at) "
            "SELECT id, type, name, metadata, created_at FROM graph_nodes_legacy"
        )
        self._db.execute("DROP TABLE graph_nodes_legacy")
        self._db.commit()

    def _migrate_legacy_graph_edges_check(self) -> None:
        """Rebuild graph_edges if it exists with the pre-Phase-5 CHECK constraint
        that restricted edge_type to a fixed vocabulary. Phase 5 makes edge_type
        open TEXT; validity is enforced in application code against the
        adapter-extensible vocabulary (smos/ingest/adapters/registry.py). Only
        called from _init_schema, which already holds self._lock.
        """
        row = self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='graph_edges'"
        ).fetchone()
        if row is None or row[0] is None or "CHECK(edge_type" not in row[0]:
            return  # table doesn't exist yet, or already migrated
        self._db.execute("ALTER TABLE graph_edges RENAME TO graph_edges_legacy")
        self._db.execute("""
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
        self._db.execute(
            "INSERT INTO graph_edges (id, source_id, target_id, edge_type, weight, metadata, created_at) "
            "SELECT id, source_id, target_id, edge_type, weight, metadata, created_at FROM graph_edges_legacy"
        )
        self._db.execute("DROP TABLE graph_edges_legacy")
        self._db.commit()

    def add_node(
        self, node_id: str, type: str, name: str, metadata: Optional[dict] = None
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO graph_nodes (id, type, name, metadata) VALUES (?,?,?,?)",
                (node_id, type, name, json.dumps(metadata or {})),
            )
            self._db.commit()

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        weight: float = 1.0,
        metadata: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO graph_edges (source_id, target_id, edge_type, weight, metadata) "
                "VALUES (?,?,?,?,?)",
                (source_id, target_id, edge_type, weight, json.dumps(metadata or {})),
            )
            self._db.commit()

    def get_node(self, node_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, type, name, metadata, created_at FROM graph_nodes WHERE id = ?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "type": row[1],
            "name": row[2],
            "metadata": json.loads(row[3]) if row[3] else {},
            "created_at": row[4],
        }

    def remove_node(self, node_id: str) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM graph_edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id),
            )
            self._db.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            self._db.commit()

    def node_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]

    def edge_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]

    def traverse(
        self,
        start_id: str,
        max_hops: int = 2,
        edge_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """BFS from start_id, treating edges as undirected. Terminates because
        `hop` is strictly bounded by max_hops regardless of graph cycles."""
        if max_hops < 1:
            return []

        edge_filter_sql = ""
        params: list = [start_id, max_hops]
        if edge_types:
            placeholders = ",".join("?" for _ in edge_types)
            edge_filter_sql = f"AND e.edge_type IN ({placeholders})"
            params.extend(edge_types)

        query = f"""
            WITH RECURSIVE traversal(node_id, hop) AS (
                SELECT ?, 0
                UNION
                SELECT
                    CASE WHEN e.source_id = t.node_id THEN e.target_id ELSE e.source_id END,
                    t.hop + 1
                FROM graph_edges e
                JOIN traversal t
                  ON (e.source_id = t.node_id OR e.target_id = t.node_id)
                WHERE t.hop < ?
                {edge_filter_sql}
            )
            SELECT n.id, n.type, n.name, n.metadata, MIN(tr.hop) as hop
            FROM (SELECT node_id, hop FROM traversal WHERE hop > 0) tr
            JOIN graph_nodes n ON n.id = tr.node_id
            GROUP BY n.id
            ORDER BY hop, n.id
        """
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [
            {
                "id": r[0],
                "type": r[1],
                "name": r[2],
                "metadata": json.loads(r[3]) if r[3] else {},
                "hop": r[4],
            }
            for r in rows
        ]

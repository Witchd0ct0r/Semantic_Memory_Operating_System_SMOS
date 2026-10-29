from __future__ import annotations

import sqlite3

from smos.memory.graph_store import GraphStore
from smos.storage.migrations import STORE_MIGRATIONS, run_migrations


def migrated_graph(conn: sqlite3.Connection) -> GraphStore:
    """A GraphStore on a connection whose schema comes from the store migrations (as in production)."""
    run_migrations(conn, STORE_MIGRATIONS)
    return GraphStore(conn)

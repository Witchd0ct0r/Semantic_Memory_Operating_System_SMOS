from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from smos.storage.migrations import Migration, run_migrations


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _baseline(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE admins (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE tokens (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            token_hash    TEXT UNIQUE NOT NULL,
            created_at    TEXT NOT NULL,
            last_used_at  TEXT,
            revoked       INTEGER NOT NULL DEFAULT 0,
            invitation_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE invitations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            label               TEXT NOT NULL,
            secret_hash         TEXT UNIQUE NOT NULL,
            created_by          TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            expires_at          TEXT NOT NULL,
            revoked             INTEGER NOT NULL DEFAULT 0,
            claimed_at          TEXT,
            claimed_ip          TEXT,
            claimed_user_agent  TEXT,
            token_id            INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE usage_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            token_id    INTEGER,
            method      TEXT NOT NULL,
            path        TEXT NOT NULL,
            status      INTEGER NOT NULL,
            latency_ms  REAL NOT NULL,
            req_bytes   INTEGER NOT NULL DEFAULT 0,
            resp_bytes  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX idx_usage_ts ON usage_log (ts)")
    conn.execute("CREATE INDEX idx_usage_token ON usage_log (token_id)")
    conn.execute("""
        CREATE TABLE settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


ADMIN_MIGRATIONS: list[Migration] = [(1, "baseline", _baseline)]


def open_admin_db(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "admin.db"
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    run_migrations(conn, ADMIN_MIGRATIONS, path)
    return conn

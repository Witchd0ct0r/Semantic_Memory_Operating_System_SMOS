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


def _add_scope(conn: sqlite3.Connection) -> None:
    """Version 2: token scopes ('read' or 'rw'). Anything that already exists keeps the
    full access it had before scopes existed, so an upgrade never locks a parser out."""
    conn.execute("ALTER TABLE tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'rw'")
    conn.execute("ALTER TABLE invitations ADD COLUMN scope TEXT NOT NULL DEFAULT 'rw'")


def _add_samples(conn: sqlite3.Connection) -> None:
    """Version 3: one row per minute of hardware and store measurements for the dashboard."""
    conn.execute("""
        CREATE TABLE samples (
            ts             TEXT PRIMARY KEY,
            cpu_percent    REAL,
            cpu_cores      REAL,
            mem_used       INTEGER,
            mem_limit      INTEGER,
            disk_used      INTEGER,
            disk_total     INTEGER,
            store_db_bytes INTEGER,
            index_bytes    INTEGER,
            admin_db_bytes INTEGER,
            documents      INTEGER,
            segments       INTEGER
        )
    """)


def _add_update_log(conn: sqlite3.Connection) -> None:
    """Version 4: one row per self-update attempt (who asked, from/to, how it ended)."""
    conn.execute("""
        CREATE TABLE update_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            at           TEXT NOT NULL,
            admin_user   TEXT NOT NULL,
            from_version TEXT NOT NULL,
            to_version   TEXT NOT NULL,
            sha          TEXT NOT NULL DEFAULT '',
            outcome      TEXT NOT NULL,
            detail       TEXT NOT NULL DEFAULT ''
        )
    """)


def _add_benchmark_runs(conn: sqlite3.Connection) -> None:
    """Version 5: one row per benchmark run (status, preset, the hardware and the full result as JSON)."""
    conn.execute("""
        CREATE TABLE benchmark_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            status        TEXT NOT NULL,
            preset        TEXT NOT NULL,
            budget_ms     REAL NOT NULL,
            smos_version  TEXT NOT NULL,
            hardware_json TEXT,
            result_json   TEXT,
            error         TEXT NOT NULL DEFAULT ''
        )
    """)


ADMIN_MIGRATIONS: list[Migration] = [
    (1, "baseline", _baseline),
    (2, "token_scope", _add_scope),
    (3, "samples", _add_samples),
    (4, "update_log", _add_update_log),
    (5, "benchmark_runs", _add_benchmark_runs),
]


def open_admin_db(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "admin.db"
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    run_migrations(conn, ADMIN_MIGRATIONS, path)
    return conn

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from smos.admin.db import now_iso


def record(
    conn: sqlite3.Connection,
    *,
    token_id: Optional[int],
    method: str,
    path: str,
    status: int,
    latency_ms: float,
    req_bytes: int,
    resp_bytes: int,
    ts: Optional[str] = None,
) -> None:
    """One row per API call. Bodies are never stored, only sizes."""
    conn.execute(
        "INSERT INTO usage_log (ts, token_id, method, path, status, latency_ms, req_bytes, resp_bytes) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ts or now_iso(), token_id, method, path, status, latency_ms, req_bytes, resp_bytes),
    )
    conn.commit()


def per_token_summary(conn: sqlite3.Connection, since_days: int = 30) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    rows = conn.execute(
        """
        SELECT t.id, t.name, COUNT(u.id), SUM(CASE WHEN u.status >= 400 THEN 1 ELSE 0 END),
               MAX(u.ts), COALESCE(SUM(u.req_bytes + u.resp_bytes), 0)
        FROM tokens t LEFT JOIN usage_log u ON u.token_id = t.id AND u.ts >= ?
        GROUP BY t.id ORDER BY t.id
        """,
        (cutoff,),
    ).fetchall()
    return [
        {"token_id": r[0], "name": r[1], "calls": r[2], "errors": r[3] or 0,
         "last_seen": r[4], "bytes": r[5]}
        for r in rows
    ]


def recent(conn: sqlite3.Connection, token_id: Optional[int], limit: int = 100) -> list[dict]:
    sql = ("SELECT ts, token_id, method, path, status, latency_ms, req_bytes, resp_bytes "
           "FROM usage_log")
    params: list = []
    if token_id is not None:
        sql += " WHERE token_id = ?"
        params.append(token_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [
        {"ts": r[0], "token_id": r[1], "method": r[2], "path": r[3], "status": r[4],
         "latency_ms": r[5], "req_bytes": r[6], "resp_bytes": r[7]}
        for r in conn.execute(sql, params).fetchall()
    ]


def prune_batch(conn: sqlite3.Connection, retention_days: int, limit: int) -> int:
    """Delete at most `limit` rows older than the retention window; returns how many.
    Small batches let callers release shared locks between deletes."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cursor = conn.execute(
        "DELETE FROM usage_log WHERE id IN (SELECT id FROM usage_log WHERE ts < ? LIMIT ?)",
        (cutoff, limit),
    )
    conn.commit()
    return cursor.rowcount


def prune(conn: sqlite3.Connection, retention_days: int, batch_size: int = 5000) -> int:
    total = 0
    while True:
        deleted = prune_batch(conn, retention_days, batch_size)
        total += deleted
        if deleted < batch_size:
            return total


def prune_samples_batch(conn: sqlite3.Connection, retention_days: int, limit: int) -> int:
    """Delete at most `limit` dashboard samples older than the retention window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cursor = conn.execute(
        "DELETE FROM samples WHERE ts IN (SELECT ts FROM samples WHERE ts < ? LIMIT ?)",
        (cutoff, limit),
    )
    conn.commit()
    return cursor.rowcount

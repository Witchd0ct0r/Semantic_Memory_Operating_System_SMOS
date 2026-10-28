from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Optional

from smos.admin.db import now_iso

TOKEN_PREFIX = "smos_"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def insert_token(conn: sqlite3.Connection, name: str, invitation_id: Optional[int] = None) -> tuple[int, str]:
    """Insert a token row WITHOUT committing, so callers can make it atomic with
    other writes. Returns (id, plaintext); only the hash is stored."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    cursor = conn.execute(
        "INSERT INTO tokens (name, token_hash, created_at, invitation_id) VALUES (?,?,?,?)",
        (name, hash_secret(plaintext), now_iso(), invitation_id),
    )
    return int(cursor.lastrowid), plaintext


def create_token(conn: sqlite3.Connection, name: str) -> tuple[int, str]:
    token_id, plaintext = insert_token(conn, name)
    conn.commit()
    return token_id, plaintext


def list_tokens(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT t.id, t.name, t.created_at, t.last_used_at, t.revoked,
               i.claimed_at, i.claimed_ip, i.claimed_user_agent
        FROM tokens t LEFT JOIN invitations i ON i.token_id = t.id
        ORDER BY t.id
    """).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "created_at": r[2], "last_used_at": r[3],
            "revoked": bool(r[4]), "claimed_at": r[5], "claimed_ip": r[6],
            "claimed_user_agent": r[7],
        }
        for r in rows
    ]


def revoke_token(conn: sqlite3.Connection, token_id: int) -> bool:
    cursor = conn.execute("UPDATE tokens SET revoked = 1 WHERE id = ?", (token_id,))
    conn.commit()
    return cursor.rowcount == 1


def authenticate(conn: sqlite3.Connection, presented: str) -> Optional[int]:
    presented = (presented or "").strip()
    if not presented:
        return None
    row = conn.execute(
        "SELECT id, revoked FROM tokens WHERE token_hash = ?", (hash_secret(presented),)
    ).fetchone()
    if row is None or row[1]:
        return None
    conn.execute("UPDATE tokens SET last_used_at = ? WHERE id = ?", (now_iso(), row[0]))
    conn.commit()
    return int(row[0])

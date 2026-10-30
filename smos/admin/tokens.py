from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from smos.admin.db import now_iso

TOKEN_PREFIX = "smos_"
SCOPES = ("read", "rw")
TOKEN_TTL_DAYS = 30
EXTEND_WINDOW_DAYS = 3


def new_expiry(base: Optional[datetime] = None) -> str:
    """ISO UTC time TOKEN_TTL_DAYS after `base` (now by default)."""
    return ((base or datetime.now(timezone.utc)) + timedelta(days=TOKEN_TTL_DAYS)).isoformat()


def check_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    return scope


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def insert_token(
    conn: sqlite3.Connection, name: str, invitation_id: Optional[int] = None, scope: str = "rw"
) -> tuple[int, str]:
    """Insert a token row WITHOUT committing, so callers can make it atomic with
    other writes. Returns (id, plaintext); only the hash is stored."""
    check_scope(scope)
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    cursor = conn.execute(
        "INSERT INTO tokens (name, token_hash, created_at, invitation_id, scope, expires_at) VALUES (?,?,?,?,?,?)",
        (name, hash_secret(plaintext), now_iso(), invitation_id, scope, new_expiry()),
    )
    return int(cursor.lastrowid), plaintext


def create_token(conn: sqlite3.Connection, name: str, scope: str = "rw") -> tuple[int, str]:
    token_id, plaintext = insert_token(conn, name, scope=scope)
    conn.commit()
    return token_id, plaintext


def list_tokens(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT t.id, t.name, t.created_at, t.last_used_at, t.revoked, t.scope,
               i.claimed_at, i.claimed_ip, i.claimed_user_agent, t.expires_at, t.rotating
        FROM tokens t LEFT JOIN invitations i ON i.id = (
            SELECT MAX(id) FROM invitations WHERE token_id = t.id AND claimed_at IS NOT NULL)
        ORDER BY t.id
    """).fetchall()
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        remaining = (datetime.fromisoformat(r[9]) - now) if r[9] else timedelta(seconds=-1)
        expired = remaining <= timedelta(0)
        state = "revoked" if r[4] else "rotating" if r[10] else "expired" if expired else "active"
        out.append({
            "id": r[0], "name": r[1], "created_at": r[2], "last_used_at": r[3],
            "revoked": bool(r[4]), "scope": r[5], "claimed_at": r[6], "claimed_ip": r[7],
            "claimed_user_agent": r[8], "expires_at": r[9], "rotating": bool(r[10]),
            "days_left": None if expired else int(remaining.total_seconds() // 86400),
            "state": state,
            "can_extend": state == "active" and remaining <= timedelta(days=EXTEND_WINDOW_DAYS),
        })
    return out


def extend_token(conn: sqlite3.Connection, token_id: int) -> bool:
    """Add TOKEN_TTL_DAYS to the expiry of an active token that expires within
    EXTEND_WINDOW_DAYS. An expired, revoked or rotating token cannot be extended."""
    now = datetime.now(timezone.utc)
    row = conn.execute("SELECT expires_at, revoked, rotating FROM tokens WHERE id = ?", (token_id,)).fetchone()
    if row is None or row[1] or row[2] or not row[0]:
        return False
    current = datetime.fromisoformat(row[0])
    if current <= now or current - now > timedelta(days=EXTEND_WINDOW_DAYS):
        return False
    cursor = conn.execute(
        "UPDATE tokens SET expires_at = ? WHERE id = ? AND expires_at = ? AND revoked = 0 AND rotating = 0",
        (new_expiry(current), token_id, row[0]))
    conn.commit()
    return cursor.rowcount == 1


def revoke_token(conn: sqlite3.Connection, token_id: int) -> bool:
    cursor = conn.execute("UPDATE tokens SET revoked = 1 WHERE id = ?", (token_id,))
    conn.commit()
    return cursor.rowcount == 1


def authenticate_with_scope(conn: sqlite3.Connection, presented: str) -> Optional[tuple[int, str]]:
    """(token id, scope) for a valid, unrevoked token; None otherwise."""
    presented = (presented or "").strip()
    if not presented:
        return None
    row = conn.execute(
        "SELECT id, revoked, scope, rotating, expires_at FROM tokens WHERE token_hash = ?",
        (hash_secret(presented),),
    ).fetchone()
    # revoked, rotating, expired and unknown all answer the same, and none updates last_used_at
    if row is None or row[1] or row[3] or not row[4] or row[4] <= now_iso():
        return None
    conn.execute("UPDATE tokens SET last_used_at = ? WHERE id = ?", (now_iso(), row[0]))
    conn.commit()
    return int(row[0]), row[2]


def authenticate(conn: sqlite3.Connection, presented: str) -> Optional[int]:
    found = authenticate_with_scope(conn, presented)
    return found[0] if found else None


def token_scope(conn: sqlite3.Connection, token_id: int) -> Optional[str]:
    row = conn.execute("SELECT scope FROM tokens WHERE id = ?", (token_id,)).fetchone()
    return row[0] if row else None

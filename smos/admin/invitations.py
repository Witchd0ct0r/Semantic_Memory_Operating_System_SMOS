from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from smos.admin.db import now_iso
from smos.admin.tokens import check_scope, hash_secret, insert_token


def create_invitation(
    conn: sqlite3.Connection, label: str, admin_username: str, ttl_hours: int = 72, scope: str = "read"
) -> tuple[int, str]:
    """Create a single-use invitation. The secret is returned once (it goes into
    the link the admin sends); only its hash is stored."""
    check_scope(scope)
    secret = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    cursor = conn.execute(
        "INSERT INTO invitations (label, secret_hash, created_by, created_at, expires_at, scope) "
        "VALUES (?,?,?,?,?,?)",
        (label, hash_secret(secret), admin_username, now_iso(), expires.isoformat(), scope),
    )
    conn.commit()
    return int(cursor.lastrowid), secret


def is_claimable(conn: sqlite3.Connection, secret: str) -> bool:
    if not secret:
        return False
    row = conn.execute(
        "SELECT 1 FROM invitations WHERE secret_hash = ? AND claimed_at IS NULL "
        "AND revoked = 0 AND expires_at > ?",
        (hash_secret(secret), now_iso()),
    ).fetchone()
    return row is not None


def claimable_scope(conn: sqlite3.Connection, secret: str) -> Optional[str]:
    """The scope the invitation would grant, or None when it cannot be claimed."""
    if not secret:
        return None
    row = conn.execute(
        "SELECT scope FROM invitations WHERE secret_hash = ? AND claimed_at IS NULL "
        "AND revoked = 0 AND expires_at > ?",
        (hash_secret(secret), now_iso()),
    ).fetchone()
    return row[0] if row else None


def claim(
    conn: sqlite3.Connection, secret: str, client_ip: str, user_agent: str
) -> Optional[tuple[int, str]]:
    """Atomically consume an invitation and create the user's token.
    Returns (token_id, plaintext) for the first successful claim, else None."""
    if not secret:
        return None
    secret_hash = hash_secret(secret)
    now = now_iso()
    cursor = conn.execute(
        "UPDATE invitations SET claimed_at = ?, claimed_ip = ?, claimed_user_agent = ? "
        "WHERE secret_hash = ? AND claimed_at IS NULL AND revoked = 0 AND expires_at > ?",
        (now, client_ip, user_agent, secret_hash, now),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        return None
    row = conn.execute(
        "SELECT id, label, scope FROM invitations WHERE secret_hash = ?", (secret_hash,)
    ).fetchone()
    token_id, plaintext = insert_token(conn, row[1], invitation_id=row[0], scope=row[2])
    conn.execute("UPDATE invitations SET token_id = ? WHERE id = ?", (token_id, row[0]))
    conn.commit()
    return token_id, plaintext


def _status(claimed_at, revoked, expires_at, now: str) -> str:
    if claimed_at:
        return "claimed"
    if revoked:
        return "revoked"
    if expires_at <= now:
        return "expired"
    return "pending"


def list_invitations(conn: sqlite3.Connection) -> list[dict]:
    now = now_iso()
    rows = conn.execute(
        "SELECT id, label, created_by, created_at, expires_at, revoked, claimed_at, "
        "claimed_ip, claimed_user_agent, token_id, scope FROM invitations ORDER BY id DESC"
    ).fetchall()
    return [
        {
            "id": r[0], "label": r[1], "created_by": r[2], "created_at": r[3],
            "expires_at": r[4], "status": _status(r[6], r[5], r[4], now),
            "claimed_at": r[6], "claimed_ip": r[7], "claimed_user_agent": r[8],
            "token_id": r[9], "scope": r[10],
        }
        for r in rows
    ]


def revoke_invitation(conn: sqlite3.Connection, invitation_id: int) -> bool:
    cursor = conn.execute(
        "UPDATE invitations SET revoked = 1 WHERE id = ? AND claimed_at IS NULL AND revoked = 0",
        (invitation_id,),
    )
    conn.commit()
    return cursor.rowcount == 1

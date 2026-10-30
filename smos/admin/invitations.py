from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from smos.admin.db import now_iso
from smos.admin.tokens import TOKEN_PREFIX, check_scope, hash_secret, insert_token, new_expiry


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


def create_rotation_invitation(
    conn: sqlite3.Connection, token_id: int, admin_username: str, ttl_hours: int = 72
) -> Optional[tuple[int, str]]:
    """Take a token out of service and create the single-use link its user claims to get a new
    secret. Returns (invitation_id, secret), or None when the token is unknown or revoked.
    One transaction: the old secret is dead from the moment this commits, and an earlier
    unclaimed rotation link for the same token is revoked."""
    row = conn.execute("SELECT name, scope FROM tokens WHERE id = ? AND revoked = 0", (token_id,)).fetchone()
    if row is None:
        return None
    secret = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    try:
        conn.execute("UPDATE tokens SET rotating = 1 WHERE id = ? AND revoked = 0", (token_id,))
        conn.execute("UPDATE invitations SET revoked = 1 WHERE rotate_token_id = ? AND claimed_at IS NULL",
                     (token_id,))
        cursor = conn.execute(
            "INSERT INTO invitations (label, secret_hash, created_by, created_at, expires_at, scope, rotate_token_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (row[0], hash_secret(secret), admin_username, now_iso(), expires.isoformat(), row[1], token_id),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return int(cursor.lastrowid), secret


def is_rotation_invitation(conn: sqlite3.Connection, secret: str) -> bool:
    if not secret:
        return False
    row = conn.execute(
        "SELECT 1 FROM invitations WHERE secret_hash = ? AND rotate_token_id IS NOT NULL", (hash_secret(secret),)
    ).fetchone()
    return row is not None


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
        "SELECT id, label, scope, rotate_token_id FROM invitations WHERE secret_hash = ?", (secret_hash,)
    ).fetchone()
    if row[3] is not None:  # a rotation: the existing token gets a new secret and a fresh lifetime
        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        replaced = conn.execute(
            "UPDATE tokens SET token_hash = ?, expires_at = ?, rotating = 0 WHERE id = ? AND revoked = 0",
            (hash_secret(plaintext), new_expiry(), row[3]),
        )
        if replaced.rowcount != 1:  # revoked meanwhile: nothing is revived, the claim is undone
            conn.rollback()
            return None
        token_id = int(row[3])
    else:
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
        "claimed_ip, claimed_user_agent, token_id, scope, rotate_token_id FROM invitations ORDER BY id DESC"
    ).fetchall()
    return [
        {
            "id": r[0], "label": r[1], "created_by": r[2], "created_at": r[3],
            "expires_at": r[4], "status": _status(r[6], r[5], r[4], now),
            "claimed_at": r[6], "claimed_ip": r[7], "claimed_user_agent": r[8],
            "token_id": r[9], "scope": r[10], "rotation": r[11] is not None,
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

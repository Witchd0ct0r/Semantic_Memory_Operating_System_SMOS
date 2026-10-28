from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from smos.admin.db import now_iso

_N, _R, _P = 2 ** 14, 8, 1


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def _check_password(password: str, stored: str) -> bool:
    try:
        _scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def has_admin(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None


def bootstrap_admin(conn: sqlite3.Connection, username: str, password: str) -> bool:
    """Create the first admin; does nothing when an admin already exists."""
    if has_admin(conn):
        return False
    conn.execute(
        "INSERT INTO admins (username, password_hash, created_at) VALUES (?,?,?)",
        (username, _hash_password(password), now_iso()),
    )
    conn.commit()
    return True


def verify_login(conn: sqlite3.Connection, username: str, password: str) -> bool:
    if not username or not password:
        return False
    row = conn.execute(
        "SELECT password_hash FROM admins WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        # spend comparable time so unknown users are not distinguishable by latency
        _hash_password(password)
        return False
    return _check_password(password, row[0])


def get_session_secret(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = 'session_secret'").fetchone()
    if row:
        return row[0]
    value = secrets.token_urlsafe(48)
    conn.execute("INSERT INTO settings (key, value) VALUES ('session_secret', ?)", (value,))
    conn.commit()
    return value

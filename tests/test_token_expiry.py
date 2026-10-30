from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smos.admin import tokens
from smos.admin.db import ADMIN_MIGRATIONS, now_iso, open_admin_db
from smos.storage.migrations import run_migrations


@pytest.fixture
def conn(tmp_path: Path):
    c = open_admin_db(tmp_path)
    yield c
    c.close()


def _at(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _set_expiry(conn: sqlite3.Connection, token_id: int, delta: timedelta) -> None:
    conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?", (_at(delta), token_id))
    conn.commit()


def _expires(conn: sqlite3.Connection, token_id: int) -> datetime:
    return datetime.fromisoformat(conn.execute("SELECT expires_at FROM tokens WHERE id = ?", (token_id,)).fetchone()[0])


def test_new_token_expires_in_30_days(conn) -> None:
    token_id, _ = tokens.create_token(conn, "agent")
    expected = datetime.now(timezone.utc) + timedelta(days=tokens.TOKEN_TTL_DAYS)
    assert abs((_expires(conn, token_id) - expected).total_seconds()) < 60
    assert tokens.TOKEN_TTL_DAYS == 30 and tokens.EXTEND_WINDOW_DAYS == 3


def test_authenticate_rejects_expired_token_without_touching_last_used(conn) -> None:
    token_id, plaintext = tokens.create_token(conn, "agent")
    _set_expiry(conn, token_id, timedelta(seconds=-1))  # a token that expired a moment ago is expired
    assert tokens.authenticate(conn, plaintext) is None
    assert conn.execute("SELECT last_used_at FROM tokens WHERE id = ?", (token_id,)).fetchone()[0] is None


def test_authenticate_rejects_rotating_token(conn) -> None:
    token_id, plaintext = tokens.create_token(conn, "agent")
    conn.execute("UPDATE tokens SET rotating = 1 WHERE id = ?", (token_id,))
    conn.commit()
    assert tokens.authenticate(conn, plaintext) is None
    assert conn.execute("SELECT last_used_at FROM tokens WHERE id = ?", (token_id,)).fetchone()[0] is None


def test_extend_only_inside_window(conn) -> None:
    far, _ = tokens.create_token(conn, "far")
    near, _ = tokens.create_token(conn, "near")
    expired, _ = tokens.create_token(conn, "expired")
    revoked, _ = tokens.create_token(conn, "revoked")
    rotating, _ = tokens.create_token(conn, "rotating")
    _set_expiry(conn, far, timedelta(days=10))
    _set_expiry(conn, near, timedelta(days=2))
    _set_expiry(conn, expired, timedelta(days=-1))
    _set_expiry(conn, revoked, timedelta(days=2))
    _set_expiry(conn, rotating, timedelta(days=2))
    tokens.revoke_token(conn, revoked)
    conn.execute("UPDATE tokens SET rotating = 1 WHERE id = ?", (rotating,))
    conn.commit()

    before_far = _expires(conn, far)
    before_near = _expires(conn, near)
    assert tokens.extend_token(conn, far) is False
    assert _expires(conn, far) == before_far
    assert tokens.extend_token(conn, near) is True
    assert _expires(conn, near) == before_near + timedelta(days=30)
    for blocked in (expired, revoked, rotating, 9999):
        assert tokens.extend_token(conn, blocked) is False


def test_list_tokens_state_and_can_extend(conn) -> None:
    ids = {name: tokens.create_token(conn, name)[0] for name in ("active", "near", "expired", "rotating", "revoked")}
    _set_expiry(conn, ids["near"], timedelta(days=2))
    _set_expiry(conn, ids["expired"], timedelta(days=-1))
    conn.execute("UPDATE tokens SET rotating = 1 WHERE id = ?", (ids["rotating"],))
    conn.commit()
    tokens.revoke_token(conn, ids["revoked"])

    rows = {r["name"]: r for r in tokens.list_tokens(conn)}
    assert {n: r["state"] for n, r in rows.items()} == {
        "active": "active", "near": "active", "expired": "expired", "rotating": "rotating", "revoked": "revoked"}
    assert {n for n, r in rows.items() if r["can_extend"]} == {"near"}
    assert rows["near"]["days_left"] == 1 or rows["near"]["days_left"] == 2
    assert rows["active"]["rotating"] is False and rows["rotating"]["rotating"] is True
    assert rows["active"]["expires_at"]


def test_migration_6_gives_existing_tokens_30_days(tmp_path: Path) -> None:
    path = tmp_path / "admin.db"
    old = sqlite3.connect(str(path))
    run_migrations(old, ADMIN_MIGRATIONS[:5], path)  # a deployment still on schema version 5
    old.execute("INSERT INTO tokens (name, token_hash, created_at) VALUES ('legacy', 'h1', 't')")
    old.commit()
    old.close()

    upgraded = open_admin_db(tmp_path)
    expires, rotating = upgraded.execute("SELECT expires_at, rotating FROM tokens").fetchone()
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((datetime.fromisoformat(expires) - expected).total_seconds()) < 120
    assert rotating == 0
    upgraded.execute("SELECT rotate_token_id FROM invitations")  # the column exists
    assert ADMIN_MIGRATIONS[-1][:2] == (6, "token_lifecycle")
    upgraded.close()

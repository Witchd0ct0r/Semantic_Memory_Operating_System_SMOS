from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smos.admin import invitations, tokens, usage
from smos.admin.db import open_admin_db


@pytest.fixture
def conn(tmp_path: Path):
    c = open_admin_db(tmp_path)
    yield c
    c.close()


def _token(conn: sqlite3.Connection, name: str = "agent", scope: str = "rw") -> tuple[int, str]:
    return tokens.create_token(conn, name, scope=scope)


def test_rotate_kills_old_secret_at_once(conn) -> None:
    token_id, plaintext = _token(conn)
    assert tokens.authenticate(conn, plaintext) == token_id
    assert invitations.create_rotation_invitation(conn, token_id, "admin") is not None
    assert tokens.authenticate(conn, plaintext) is None


def test_claim_restores_same_token_with_new_secret(conn) -> None:
    token_id, old = _token(conn, "parser-a", scope="read")
    usage.record(conn, token_id=token_id, method="GET", path="/x", status=200, latency_ms=1.0, req_bytes=0, resp_bytes=0)
    conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), token_id))
    conn.commit()
    _inv, secret = invitations.create_rotation_invitation(conn, token_id, "admin")

    assert invitations.is_rotation_invitation(conn, secret) is True
    claimed = invitations.claim(conn, secret, "10.0.0.5", "UA/1")
    assert claimed is not None
    new_id, new_plain = claimed
    assert new_id == token_id and new_plain != old
    assert tokens.authenticate(conn, new_plain) == token_id
    assert tokens.authenticate(conn, old) is None
    row = conn.execute("SELECT name, scope, rotating, expires_at FROM tokens WHERE id = ?", (token_id,)).fetchone()
    assert row[:3] == ("parser-a", "read", 0)
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((datetime.fromisoformat(row[3]) - expected).total_seconds()) < 60  # fresh 30 days, not the 1 day left
    assert conn.execute("SELECT COUNT(*) FROM usage_log WHERE token_id = ?", (token_id,)).fetchone()[0] == 1
    inv = conn.execute("SELECT claimed_ip, claimed_user_agent, token_id FROM invitations WHERE secret_hash = ?",
                       (tokens.hash_secret(secret),)).fetchone()
    assert inv == ("10.0.0.5", "UA/1", token_id)
    assert len([t for t in tokens.list_tokens(conn) if t["id"] == token_id]) == 1  # still one row in the list


def test_expired_token_can_be_rotated_and_recovered(conn) -> None:
    token_id, old = _token(conn)
    conn.execute("UPDATE tokens SET expires_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), token_id))
    conn.commit()
    _inv, secret = invitations.create_rotation_invitation(conn, token_id, "admin")
    _id, new_plain = invitations.claim(conn, secret, "ip", "ua")
    assert tokens.authenticate(conn, new_plain) == token_id


def test_new_rotation_supersedes_old_link(conn) -> None:
    token_id, _ = _token(conn)
    _a, first = invitations.create_rotation_invitation(conn, token_id, "admin")
    _b, second = invitations.create_rotation_invitation(conn, token_id, "admin")
    assert invitations.claim(conn, first, "ip", "ua") is None
    assert invitations.claim(conn, second, "ip", "ua") is not None


def test_replayed_rotation_link_fails(conn) -> None:
    token_id, _ = _token(conn)
    _inv, secret = invitations.create_rotation_invitation(conn, token_id, "admin")
    first = invitations.claim(conn, secret, "ip", "ua")
    assert first is not None
    assert invitations.claim(conn, secret, "ip", "ua") is None
    assert tokens.authenticate(conn, first[1]) == token_id  # the replay did not kill the new secret


def test_rotation_link_after_revoke_fails_and_does_not_revive(conn) -> None:
    token_id, _ = _token(conn)
    _inv, secret = invitations.create_rotation_invitation(conn, token_id, "admin")
    tokens.revoke_token(conn, token_id)
    before = conn.execute("SELECT token_hash FROM tokens WHERE id = ?", (token_id,)).fetchone()[0]
    assert invitations.claim(conn, secret, "ip", "ua") is None
    assert conn.execute("SELECT token_hash, revoked, rotating FROM tokens WHERE id = ?", (token_id,)).fetchone() == (before, 1, 1)
    assert conn.execute("SELECT claimed_at FROM invitations WHERE secret_hash = ?",
                        (tokens.hash_secret(secret),)).fetchone()[0] is None  # the failed claim was rolled back


def test_rotation_for_revoked_or_unknown_token_returns_none(conn) -> None:
    token_id, _ = _token(conn)
    tokens.revoke_token(conn, token_id)
    assert invitations.create_rotation_invitation(conn, token_id, "admin") is None
    assert invitations.create_rotation_invitation(conn, 9999, "admin") is None


def test_unclaimed_rotation_leaves_token_unusable_until_rotated_again(conn) -> None:
    token_id, old = _token(conn)
    _inv, secret = invitations.create_rotation_invitation(conn, token_id, "admin", ttl_hours=0)  # already expired
    assert invitations.claim(conn, secret, "ip", "ua") is None
    assert tokens.authenticate(conn, old) is None
    _inv2, secret2 = invitations.create_rotation_invitation(conn, token_id, "admin")
    assert invitations.claim(conn, secret2, "ip", "ua") is not None


def test_rotation_invitation_listed_with_rotation_flag(conn) -> None:
    token_id, _ = _token(conn, "listed")
    invitations.create_invitation(conn, "plain", "admin")
    invitations.create_rotation_invitation(conn, token_id, "admin")
    flags = {i["label"]: i["rotation"] for i in invitations.list_invitations(conn)}
    assert flags == {"plain": False, "listed": True}
    assert invitations.is_rotation_invitation(conn, "nope") is False

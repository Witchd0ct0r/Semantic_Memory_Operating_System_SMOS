from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from smos.admin import invitations, tokens
from smos.admin.db import open_admin_db


@pytest.fixture
def conn(tmp_path: Path):
    c = open_admin_db(tmp_path)
    yield c
    c.close()


def _all_text(conn: sqlite3.Connection) -> str:
    chunks: list[str] = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in conn.execute(f"SELECT * FROM {name}").fetchall():
            chunks.append(" ".join(str(v) for v in row))
    return "\n".join(chunks)


def test_claim_creates_token_once(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin")
    first = invitations.claim(conn, secret, "10.0.0.5", "pytest-agent")
    assert first is not None
    assert invitations.claim(conn, secret, "10.0.0.6", "other") is None
    assert len(tokens.list_tokens(conn)) == 1


def test_claim_records_ip_and_user_agent(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin")
    invitations.claim(conn, secret, "10.0.0.5", "Mozilla/5.0 test")
    row = invitations.list_invitations(conn)[0]
    assert row["status"] == "claimed"
    assert row["claimed_ip"] == "10.0.0.5"
    assert row["claimed_user_agent"] == "Mozilla/5.0 test"
    assert row["claimed_at"] is not None
    assert row["token_id"] is not None


def test_concurrent_claims_only_one_wins(tmp_path: Path) -> None:
    c0 = open_admin_db(tmp_path)
    _id, secret = invitations.create_invitation(c0, "alice", "admin")
    results: list = []

    def worker() -> None:
        c = open_admin_db(tmp_path)
        try:
            results.append(invitations.claim(c, secret, "1.1.1.1", "ua"))
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in results if r is not None]) == 1
    assert len(tokens.list_tokens(c0)) == 1
    c0.close()


def test_expired_invitation_not_claimable(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin", ttl_hours=-1)
    assert invitations.is_claimable(conn, secret) is False
    assert invitations.claim(conn, secret, "1.1.1.1", "ua") is None
    assert invitations.list_invitations(conn)[0]["status"] == "expired"
    assert tokens.list_tokens(conn) == []


def test_revoked_invitation_not_claimable(conn) -> None:
    inv_id, secret = invitations.create_invitation(conn, "alice", "admin")
    assert invitations.revoke_invitation(conn, inv_id) is True
    assert invitations.is_claimable(conn, secret) is False
    assert invitations.claim(conn, secret, "1.1.1.1", "ua") is None
    assert invitations.list_invitations(conn)[0]["status"] == "revoked"


def test_wrong_secret_none(conn) -> None:
    invitations.create_invitation(conn, "alice", "admin")
    assert invitations.claim(conn, "not-the-secret", "1.1.1.1", "ua") is None
    assert invitations.is_claimable(conn, "not-the-secret") is False
    assert invitations.claim(conn, "", "1.1.1.1", "ua") is None


def test_secret_and_token_plaintext_not_stored(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin")
    claimed = invitations.claim(conn, secret, "1.1.1.1", "ua")
    assert claimed is not None
    text = _all_text(conn)
    assert secret not in text
    assert claimed[1] not in text


def test_claimed_token_inherits_label_and_authenticates(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin")
    token_id, plaintext = invitations.claim(conn, secret, "1.1.1.1", "ua")
    assert tokens.authenticate(conn, plaintext) == token_id
    row = tokens.list_tokens(conn)[0]
    assert row["name"] == "alice"
    assert row["claimed_ip"] == "1.1.1.1"


def test_list_invitations_statuses_and_no_secret_fields(conn) -> None:
    invitations.create_invitation(conn, "pending-one", "admin")
    row = invitations.list_invitations(conn)[0]
    assert row["status"] == "pending"
    assert row["created_by"] == "admin"
    assert not any("hash" in key or "secret" in key for key in row)


def test_revoke_only_while_pending(conn) -> None:
    inv_id, secret = invitations.create_invitation(conn, "alice", "admin")
    invitations.claim(conn, secret, "1.1.1.1", "ua")
    assert invitations.revoke_invitation(conn, inv_id) is False

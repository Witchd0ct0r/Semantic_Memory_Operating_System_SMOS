from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from smos.admin import invitations, tokens
from smos.admin.db import ADMIN_MIGRATIONS, open_admin_db
from smos.storage.migrations import run_migrations


@pytest.fixture
def conn(tmp_path: Path):
    c = open_admin_db(tmp_path)
    yield c
    c.close()


def test_create_token_scope_roundtrip(conn) -> None:
    rid, rtok = tokens.create_token(conn, "reader", scope="read")
    wid, wtok = tokens.create_token(conn, "writer", scope="rw")
    assert tokens.authenticate_with_scope(conn, rtok) == (rid, "read")
    assert tokens.authenticate_with_scope(conn, wtok) == (wid, "rw")
    assert tokens.authenticate(conn, rtok) == rid


def test_create_token_rejects_unknown_scope(conn) -> None:
    with pytest.raises(ValueError):
        tokens.create_token(conn, "x", scope="admin")


def test_list_tokens_shows_scope(conn) -> None:
    tokens.create_token(conn, "reader", scope="read")
    assert tokens.list_tokens(conn)[0]["scope"] == "read"


def test_revoked_token_has_no_scope_access(conn) -> None:
    tid, tok = tokens.create_token(conn, "w", scope="rw")
    tokens.revoke_token(conn, tid)
    assert tokens.authenticate_with_scope(conn, tok) is None


def test_invitation_scope_is_inherited_by_claimed_token(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin", scope="read")
    token_id, plaintext = invitations.claim(conn, secret, "1.1.1.1", "ua")
    assert tokens.authenticate_with_scope(conn, plaintext) == (token_id, "read")
    _id2, secret2 = invitations.create_invitation(conn, "parser", "admin", scope="rw")
    token_id2, plaintext2 = invitations.claim(conn, secret2, "1.1.1.1", "ua")
    assert tokens.authenticate_with_scope(conn, plaintext2) == (token_id2, "rw")


def test_invitation_default_scope_is_read(conn) -> None:
    _id, secret = invitations.create_invitation(conn, "alice", "admin")
    assert invitations.list_invitations(conn)[0]["scope"] == "read"
    assert invitations.claimable_scope(conn, secret) == "read"


def test_invitation_rejects_unknown_scope(conn) -> None:
    with pytest.raises(ValueError):
        invitations.create_invitation(conn, "alice", "admin", scope="superuser")


def test_claimable_scope_none_when_not_claimable(conn) -> None:
    inv_id, secret = invitations.create_invitation(conn, "alice", "admin", scope="rw")
    invitations.revoke_invitation(conn, inv_id)
    assert invitations.claimable_scope(conn, secret) is None
    assert invitations.claimable_scope(conn, "nope") is None


def test_token_scope_lookup(conn) -> None:
    tid, _ = tokens.create_token(conn, "w", scope="rw")
    assert tokens.token_scope(conn, tid) == "rw"
    assert tokens.token_scope(conn, 9999) is None


def test_migration_2_keeps_existing_tokens_and_invitations_rw(tmp_path: Path) -> None:
    path = tmp_path / "admin.db"
    old = sqlite3.connect(str(path))
    run_migrations(old, ADMIN_MIGRATIONS[:1], path)  # a deployment still on schema version 1
    old.execute("INSERT INTO tokens (name, token_hash, created_at) VALUES ('legacy', 'h1', 't')")
    old.execute("INSERT INTO invitations (label, secret_hash, created_by, created_at, expires_at) "
                "VALUES ('legacy-inv', 'h2', 'admin', 't', '9999')")
    old.commit()
    old.close()

    upgraded = open_admin_db(tmp_path)
    assert upgraded.execute("SELECT scope FROM tokens").fetchone()[0] == "rw"
    assert upgraded.execute("SELECT scope FROM invitations").fetchone()[0] == "rw"
    assert upgraded.execute("SELECT version FROM schema_version").fetchone()[0] == ADMIN_MIGRATIONS[-1][0]
    assert (tmp_path / "admin.db.bak-v1").exists()
    upgraded.close()

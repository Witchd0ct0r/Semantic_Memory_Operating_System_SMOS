from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from smos.admin import tokens
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


def test_token_plaintext_not_stored(conn) -> None:
    _id, plaintext = tokens.create_token(conn, "agent-1")
    assert plaintext not in _all_text(conn)


def test_authenticate_valid_then_revoked(conn) -> None:
    token_id, plaintext = tokens.create_token(conn, "agent-1")
    assert tokens.authenticate(conn, plaintext) == token_id
    assert tokens.revoke_token(conn, token_id) is True
    assert tokens.authenticate(conn, plaintext) is None


def test_authenticate_unknown_and_empty_none(conn) -> None:
    tokens.create_token(conn, "agent-1")
    assert tokens.authenticate(conn, "smos_nope") is None
    assert tokens.authenticate(conn, "") is None
    assert tokens.authenticate(conn, "   ") is None


def test_authenticate_updates_last_used(conn) -> None:
    token_id, plaintext = tokens.create_token(conn, "agent-1")
    assert tokens.list_tokens(conn)[0]["last_used_at"] is None
    tokens.authenticate(conn, plaintext)
    assert tokens.list_tokens(conn)[0]["last_used_at"] is not None


def test_list_tokens_has_no_secret_fields(conn) -> None:
    tokens.create_token(conn, "agent-1")
    row = tokens.list_tokens(conn)[0]
    assert row["name"] == "agent-1"
    assert not any("hash" in key or "secret" in key or "plaintext" in key for key in row)


def test_revoke_unknown_token_false(conn) -> None:
    assert tokens.revoke_token(conn, 9999) is False

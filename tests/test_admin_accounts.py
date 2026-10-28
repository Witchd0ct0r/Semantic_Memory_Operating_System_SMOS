from __future__ import annotations

from pathlib import Path

import pytest

from smos.admin import accounts
from smos.admin.db import open_admin_db


@pytest.fixture
def conn(tmp_path: Path):
    c = open_admin_db(tmp_path)
    yield c
    c.close()


def test_bootstrap_only_once(conn) -> None:
    assert accounts.bootstrap_admin(conn, "admin", "pw-one-123") is True
    assert accounts.bootstrap_admin(conn, "other", "pw-two-456") is False
    assert accounts.verify_login(conn, "admin", "pw-one-123") is True
    assert accounts.verify_login(conn, "other", "pw-two-456") is False


def test_verify_login_wrong_password_false(conn) -> None:
    accounts.bootstrap_admin(conn, "admin", "pw-one-123")
    assert accounts.verify_login(conn, "admin", "wrong") is False
    assert accounts.verify_login(conn, "nobody", "pw-one-123") is False
    assert accounts.verify_login(conn, "admin", "") is False


def test_password_not_stored_plain(conn) -> None:
    accounts.bootstrap_admin(conn, "admin", "pw-one-123")
    stored = conn.execute("SELECT password_hash FROM admins").fetchone()[0]
    assert "pw-one-123" not in stored


def test_has_admin(conn) -> None:
    assert accounts.has_admin(conn) is False
    accounts.bootstrap_admin(conn, "admin", "pw-one-123")
    assert accounts.has_admin(conn) is True


def test_session_secret_stable_and_long(conn) -> None:
    first = accounts.get_session_secret(conn)
    assert len(first) >= 32
    assert accounts.get_session_secret(conn) == first

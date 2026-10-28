from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smos.admin import tokens, usage
from smos.admin.db import open_admin_db


@pytest.fixture
def conn(tmp_path: Path):
    c = open_admin_db(tmp_path)
    yield c
    c.close()


def _rec(conn, token_id, status=200, path="/api/v1/query", **kw) -> None:
    usage.record(conn, token_id=token_id, method="POST", path=path, status=status,
                 latency_ms=12.5, req_bytes=100, resp_bytes=900, **kw)


def test_usage_record_and_per_token_summary(conn) -> None:
    tid, _ = tokens.create_token(conn, "agent-1")
    _rec(conn, tid)
    _rec(conn, tid, status=500)
    summary = usage.per_token_summary(conn, since_days=30)
    row = next(r for r in summary if r["token_id"] == tid)
    assert row["name"] == "agent-1"
    assert row["calls"] == 2
    assert row["errors"] == 1
    assert row["last_seen"] is not None


def test_usage_unauthenticated_row_has_null_token(conn) -> None:
    _rec(conn, None, status=401)
    recent = usage.recent(conn, token_id=None, limit=10)
    assert recent[0]["token_id"] is None
    assert recent[0]["status"] == 401


def test_recent_filters_by_token(conn) -> None:
    a, _ = tokens.create_token(conn, "a")
    b, _ = tokens.create_token(conn, "b")
    _rec(conn, a)
    _rec(conn, b, path="/api/v1/recall")
    only_b = usage.recent(conn, token_id=b, limit=10)
    assert [r["path"] for r in only_b] == ["/api/v1/recall"]


def test_prune_removes_only_old_rows(conn) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    _rec(conn, None, ts=old)
    _rec(conn, None)
    assert usage.prune(conn, retention_days=90) == 1
    assert len(usage.recent(conn, token_id=None, limit=10)) == 1


def test_usage_row_stores_no_body_columns(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_log)")}
    assert not ({"body", "request_body", "response_body", "payload"} & cols)

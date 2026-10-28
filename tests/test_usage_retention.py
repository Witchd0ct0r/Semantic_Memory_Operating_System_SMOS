from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from smos.admin import usage
from smos.api.app import create_app


def _old_row(conn) -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    usage.record(conn, token_id=None, method="GET", path="/api/v1/x", status=200,
                 latency_ms=1.0, req_bytes=0, resp_bytes=0, ts=old)


def test_startup_prunes_rows_older_than_retention(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SMOS_USAGE_RETENTION_DAYS", "30")
    app = create_app(tmp_path / "d", admin_user="admin", admin_password="pw-123456789")
    _old_row(app.state.admin_db)
    app.state.close()
    app2 = create_app(tmp_path / "d", admin_user="admin", admin_password="pw-123456789")
    assert usage.recent(app2.state.admin_db, token_id=None, limit=10) == []
    app2.state.close()


def test_retention_runs_during_operation(api, monkeypatch) -> None:
    client, headers, app = api
    monkeypatch.setenv("SMOS_USAGE_RETENTION_DAYS", "30")
    _old_row(app.state.admin_db)
    app.state.last_prune = time.monotonic() - 2 * 86400  # a day has passed
    client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers)
    rows = usage.recent(app.state.admin_db, token_id=None, limit=50)
    assert all(r["path"] != "/api/v1/x" for r in rows)

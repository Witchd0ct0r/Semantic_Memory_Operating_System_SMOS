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
    thread = getattr(app.state, "prune_thread", None)
    if thread is not None:
        thread.join(timeout=10)
    rows = usage.recent(app.state.admin_db, token_id=None, limit=50)
    assert all(r["path"] != "/api/v1/x" for r in rows)


def test_prune_batch_deletes_at_most_the_limit(tmp_path) -> None:
    from smos.admin.db import open_admin_db

    conn = open_admin_db(tmp_path)
    for _ in range(25):
        _old_row(conn)
    assert usage.prune_batch(conn, retention_days=30, limit=10) == 10
    assert usage.prune_batch(conn, retention_days=30, limit=10) == 10
    assert usage.prune_batch(conn, retention_days=30, limit=10) == 5
    assert usage.prune_batch(conn, retention_days=30, limit=10) == 0
    conn.close()


def test_pruning_releases_the_admin_lock_between_batches(tmp_path, monkeypatch) -> None:
    import smos.api.app as app_module

    monkeypatch.setenv("SMOS_USAGE_RETENTION_DAYS", "30")
    monkeypatch.setattr(app_module, "_PRUNE_BATCH", 10)
    app = create_app(tmp_path / "d", admin_user="admin", admin_password="pw-123456789")
    for _ in range(45):
        _old_row(app.state.admin_db)

    held_during_batch = []
    real = usage.prune_batch

    def spy(conn, retention_days, limit):
        # the batch itself runs under the lock; between batches it must be free
        held_during_batch.append(app.state.admin_lock.acquire(blocking=False))
        if held_during_batch[-1]:
            app.state.admin_lock.release()
        return real(conn, retention_days, limit)

    monkeypatch.setattr(usage, "prune_batch", spy)
    app_module._prune_usage(app, background=True)
    app.state.prune_thread.join(timeout=10)
    assert usage.recent(app.state.admin_db, token_id=None, limit=100) == []
    assert len(held_during_batch) >= 5  # 45 rows / 10 per batch, one pass each
    app.state.close()

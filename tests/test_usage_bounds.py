from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smos.admin import tokens, usage
from smos.api import app as app_module
from smos.api.app import create_app

PASSWORD = "pw-123456789"


def _app(tmp_path, monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    app = create_app(tmp_path / "d", admin_user="admin", admin_password=PASSWORD, start_sampler=False)
    _tid, plaintext = tokens.create_token(app.state.admin_db, "t", scope="read")
    return app, TestClient(app), {"Authorization": f"Bearer {plaintext}"}


def _rows(app):
    return app.state.admin_db.execute("SELECT token_id, path FROM usage_log ORDER BY id").fetchall()


def test_unmatched_paths_are_logged_under_one_label(tmp_path, monkeypatch) -> None:
    app, client, _h = _app(tmp_path, monkeypatch)
    for name in ("probe-1", "probe-2", "wp-admin/setup.php"):
        assert client.get(f"/api/v1/{name}").status_code in (401, 404)
    paths = {path for _token, path in _rows(app)}
    assert paths == {"(unmatched)"}
    app.state.close()


def test_matched_routes_keep_their_template(tmp_path, monkeypatch) -> None:
    app, client, headers = _app(tmp_path, monkeypatch)
    client.get("/api/v1/nodes/some-id/text", headers=headers)
    assert ("/api/v1/nodes/{node_id}/text") in {path for _t, path in _rows(app)}
    app.state.close()


def test_unauthenticated_requests_have_a_per_minute_logging_budget(tmp_path, monkeypatch) -> None:
    app, client, headers = _app(tmp_path, monkeypatch, SMOS_UNAUTH_LOG_PER_MIN=5)
    for i in range(30):
        client.get(f"/api/v1/probe-{i}")
    unauth = [r for r in _rows(app) if r[0] is None]
    assert len(unauth) == 5
    assert app.state.unauth_suppressed == 25
    # authenticated calls are never suppressed
    for _ in range(3):
        client.post("/api/v1/query", json={"query": "radiated emissions"}, headers=headers)
    assert len([r for r in _rows(app) if r[0] is not None]) == 3
    app.state.close()


def test_the_budget_refills_each_minute(tmp_path, monkeypatch) -> None:
    app, client, _h = _app(tmp_path, monkeypatch, SMOS_UNAUTH_LOG_PER_MIN=2)
    for i in range(5):
        client.get(f"/api/v1/probe-{i}")
    assert len([r for r in _rows(app) if r[0] is None]) == 2
    app.state.unauth_budget._minute -= 1  # a minute passes
    for i in range(5):
        client.get(f"/api/v1/probe-again-{i}")
    assert len([r for r in _rows(app) if r[0] is None]) == 4
    app.state.close()


def test_prune_over_cap_batch_removes_the_oldest_rows_first(tmp_path) -> None:
    from smos.admin.db import open_admin_db

    conn = open_admin_db(tmp_path)
    for i in range(30):
        usage.record(conn, token_id=None, method="GET", path=f"/p{i}", status=200,
                     latency_ms=1.0, req_bytes=0, resp_bytes=0)
    assert usage.prune_over_cap_batch(conn, max_rows=20, limit=4) == 4
    assert usage.prune_over_cap_batch(conn, max_rows=20, limit=100) == 6
    assert usage.prune_over_cap_batch(conn, max_rows=20, limit=100) == 0
    kept = [r[0] for r in conn.execute("SELECT path FROM usage_log ORDER BY id")]
    assert len(kept) == 20 and kept[0] == "/p10" and kept[-1] == "/p29"
    conn.close()


def test_startup_enforces_the_row_cap(tmp_path, monkeypatch) -> None:
    app, _client, _h = _app(tmp_path, monkeypatch, SMOS_USAGE_MAX_ROWS=50)
    for i in range(120):
        usage.record(app.state.admin_db, token_id=None, method="GET", path=f"/p{i}", status=200,
                     latency_ms=1.0, req_bytes=0, resp_bytes=0)
    app_module._prune_usage(app)
    assert app.state.admin_db.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0] == 50
    app.state.close()


def test_the_cap_is_also_enforced_while_running(tmp_path, monkeypatch) -> None:
    app, client, headers = _app(tmp_path, monkeypatch, SMOS_USAGE_MAX_ROWS=20, SMOS_USAGE_PRUNE_EVERY_ROWS=10)
    for _ in range(60):
        client.post("/api/v1/query", json={"query": "radiated emissions"}, headers=headers)
    thread = getattr(app.state, "prune_thread", None)
    if thread is not None:
        thread.join(timeout=10)
    count = app.state.admin_db.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    assert count <= 20 + 10 + 1, count  # the cap plus at most one trigger interval of new rows
    app.state.close()

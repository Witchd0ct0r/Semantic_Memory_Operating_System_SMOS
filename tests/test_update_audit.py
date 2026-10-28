from __future__ import annotations

import sqlite3

from smos import launcher
from smos.admin.db import ADMIN_MIGRATIONS, open_admin_db
from smos.api.app import create_app
from smos.launcher import UpdatePaths
from smos.storage.migrations import run_migrations
from smos.update import audit


def _result(ok: bool = True, **extra) -> dict:
    base = {"ok": ok, "from_version": "0.2.0", "to_version": "0.3.0", "sha": "a" * 40,
            "requested_by": "alice", "requested_at": "2026-10-28T10:00:00+00:00",
            "finished_at": "2026-10-28T10:01:00+00:00"}
    if not ok:
        base.update(error="no healthy answer within 120 s", log_tail=["Traceback", "boom"])
    return {**base, **extra}


def test_migration_4_creates_update_log_and_keeps_rows(tmp_path) -> None:
    path = tmp_path / "admin.db"
    old = sqlite3.connect(str(path))
    run_migrations(old, ADMIN_MIGRATIONS[:3], path)
    old.execute("INSERT INTO tokens (name, token_hash, created_at, scope) VALUES ('keep', 'h', 't', 'rw')")
    old.commit()
    old.close()
    upgraded = open_admin_db(tmp_path)
    assert upgraded.execute("SELECT name FROM tokens").fetchone()[0] == "keep"
    assert upgraded.execute("SELECT version FROM schema_version").fetchone()[0] == 4
    columns = {r[1] for r in upgraded.execute("PRAGMA table_info(update_log)")}
    assert columns == {"id", "at", "admin_user", "from_version", "to_version", "sha", "outcome", "detail"}
    upgraded.close()


def test_record_and_recent_newest_first(tmp_path) -> None:
    conn = open_admin_db(tmp_path)
    audit.record(conn, admin_user="a", from_version="0.2.0", to_version="0.3.0", sha="s", outcome="refused",
                 detail="first", at="2026-01-01T00:00:00+00:00")
    audit.record(conn, admin_user="b", from_version="0.2.0", to_version="0.3.0", sha="s", outcome="failed",
                 detail="second", at="2026-01-02T00:00:00+00:00")
    rows = audit.recent(conn, limit=10)
    assert [r["detail"] for r in rows] == ["second", "first"]
    assert rows[0]["admin_user"] == "b" and rows[0]["outcome"] == "failed"
    assert len(audit.recent(conn, limit=1)) == 1
    conn.close()


def test_import_last_result_inserts_once(tmp_path) -> None:
    conn = open_admin_db(tmp_path)
    paths = UpdatePaths(tmp_path)
    launcher.write_json_atomic(paths.last_result, _result())
    assert audit.import_last_result(conn, paths) is True
    assert audit.import_last_result(conn, paths) is False
    rows = audit.recent(conn)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "applied" and rows[0]["admin_user"] == "alice"
    assert rows[0]["from_version"] == "0.2.0" and rows[0]["to_version"] == "0.3.0" and rows[0]["sha"] == "a" * 40
    assert rows[0]["at"] == "2026-10-28T10:01:00+00:00"
    conn.close()


def test_import_after_rollback_still_records_admin_and_error(tmp_path) -> None:
    conn = open_admin_db(tmp_path)  # a rollback restored an admin.db that never saw this attempt
    paths = UpdatePaths(tmp_path)
    launcher.write_json_atomic(paths.last_result, _result(ok=False))
    assert audit.import_last_result(conn, paths) is True
    row = audit.recent(conn)[0]
    assert row["outcome"] == "rolled_back" and row["admin_user"] == "alice"
    assert "no healthy answer" in row["detail"]
    conn.close()


def test_import_without_or_with_garbage_file_returns_false(tmp_path) -> None:
    conn = open_admin_db(tmp_path)
    paths = UpdatePaths(tmp_path)
    assert audit.import_last_result(conn, paths) is False
    paths.last_result.parent.mkdir(parents=True)
    paths.last_result.write_text("{not json")
    assert audit.import_last_result(conn, paths) is False
    launcher.write_json_atomic(paths.last_result, {"ok": True})  # no finished_at: nothing identifies it
    assert audit.import_last_result(conn, paths) is False
    conn.close()


def test_describe_imports_a_result_written_after_startup(api) -> None:
    """The supervisor records the result only after the new app is healthy, i.e. after its start-time import."""
    from smos.update.service import describe

    _client, _h, app = api
    assert audit.recent(app.state.admin_db) == []
    launcher.write_json_atomic(UpdatePaths(app.state.data_dir).last_result, _result())
    describe(app)
    describe(app)  # idempotent
    assert [r["outcome"] for r in audit.recent(app.state.admin_db)] == ["applied"]


def test_create_app_imports_the_last_result_on_start(tmp_path) -> None:
    data = tmp_path / "data"
    launcher.write_json_atomic(UpdatePaths(data).last_result, _result())
    app = create_app(data, admin_user="admin", admin_password="correct-horse-1", start_sampler=False)
    try:
        assert [r["outcome"] for r in audit.recent(app.state.admin_db)] == ["applied"]
    finally:
        app.state.close()

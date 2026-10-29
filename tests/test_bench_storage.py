from __future__ import annotations

import sqlite3

from smos.admin import benchmarks
from smos.admin.db import ADMIN_MIGRATIONS, open_admin_db
from smos.storage.migrations import run_migrations
from tests.benchdata import make_result

_COLUMNS = {"id", "started_at", "finished_at", "status", "preset", "budget_ms", "smos_version", "hardware_json",
            "result_json", "error"}


def _conn(tmp_path):
    return open_admin_db(tmp_path)


def test_migration_5_on_a_fresh_database(tmp_path) -> None:
    conn = _conn(tmp_path)
    assert {r[1] for r in conn.execute("PRAGMA table_info(benchmark_runs)")} == _COLUMNS
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == ADMIN_MIGRATIONS[-1][0] >= 5
    conn.close()


def test_migration_5_keeps_existing_rows_of_a_version_4_database(tmp_path) -> None:
    path = tmp_path / "admin.db"
    old = sqlite3.connect(str(path))
    run_migrations(old, ADMIN_MIGRATIONS[:4], path)
    old.execute("INSERT INTO tokens (name, token_hash, created_at, scope) VALUES ('keep', 'h', 't', 'rw')")
    old.commit()
    old.close()
    upgraded = open_admin_db(tmp_path)
    assert upgraded.execute("SELECT name FROM tokens").fetchone()[0] == "keep"
    assert {r[1] for r in upgraded.execute("PRAGMA table_info(benchmark_runs)")} == _COLUMNS
    upgraded.close()


def test_create_and_finish_roundtrip(tmp_path) -> None:
    conn = _conn(tmp_path)
    run_id = benchmarks.create_run(conn, preset="quick", budget_ms=200.0, version="0.3.3")
    assert benchmarks.has_running(conn)
    running = benchmarks.get_run(conn, run_id)
    assert running["status"] == "running" and running["result"] is None and running["finished_at"] is None
    benchmarks.finish_run(conn, run_id, status="completed", result=make_result(), hardware={"cpu_cores": 4})
    done = benchmarks.get_run(conn, run_id)
    assert done["status"] == "completed" and done["finished_at"] and done["hardware"] == {"cpu_cores": 4}
    assert done["result"]["schema"] == 1 and done["result"]["scaling"][0]["segments"] == 1000
    assert not benchmarks.has_running(conn)
    assert benchmarks.get_run(conn, 999) is None
    conn.close()


def test_failed_run_keeps_its_error(tmp_path) -> None:
    conn = _conn(tmp_path)
    run_id = benchmarks.create_run(conn, preset="quick", budget_ms=200.0, version="0.3.3")
    benchmarks.finish_run(conn, run_id, status="failed", result=None, hardware=None, error="disk full")
    run = benchmarks.get_run(conn, run_id)
    assert run["status"] == "failed" and run["error"] == "disk full" and run["result"] is None
    conn.close()


def test_previous_completed_same_preset_only_and_older_only(tmp_path) -> None:
    conn = _conn(tmp_path)
    ids = {}
    for name, preset, status in [("a", "quick", "completed"), ("b", "standard", "completed"),
                                 ("c", "quick", "failed"), ("d", "quick", "completed"), ("e", "quick", "completed")]:
        run_id = benchmarks.create_run(conn, preset=preset, budget_ms=200.0, version="0.3.3")
        benchmarks.finish_run(conn, run_id, status=status, result=make_result() if status == "completed" else None,
                              hardware=None)
        ids[name] = run_id
    assert benchmarks.previous_completed(conn, "quick", ids["e"])["id"] == ids["d"]
    assert benchmarks.previous_completed(conn, "quick", ids["d"])["id"] == ids["a"]
    assert benchmarks.previous_completed(conn, "quick", ids["a"]) is None
    assert benchmarks.previous_completed(conn, "standard", ids["e"])["id"] == ids["b"]
    conn.close()


def test_mark_running_interrupted_only_touches_running(tmp_path) -> None:
    conn = _conn(tmp_path)
    done = benchmarks.create_run(conn, preset="quick", budget_ms=200.0, version="0.3.3")
    benchmarks.finish_run(conn, done, status="completed", result=make_result(), hardware=None)
    stuck = benchmarks.create_run(conn, preset="quick", budget_ms=200.0, version="0.3.3")
    assert benchmarks.mark_running_interrupted(conn) == 1
    assert benchmarks.get_run(conn, stuck)["status"] == "interrupted"
    assert benchmarks.get_run(conn, stuck)["finished_at"]
    assert benchmarks.get_run(conn, done)["status"] == "completed"
    assert benchmarks.mark_running_interrupted(conn) == 0
    conn.close()


def test_prune_keeps_newest_50_and_never_a_running_row(tmp_path) -> None:
    conn = _conn(tmp_path)
    first = benchmarks.create_run(conn, preset="quick", budget_ms=200.0, version="0.3.3")  # stays running
    for _ in range(54):
        run_id = benchmarks.create_run(conn, preset="quick", budget_ms=200.0, version="0.3.3")
        benchmarks.finish_run(conn, run_id, status="completed", result=make_result(), hardware=None)
    deleted = benchmarks.prune(conn, keep=50)
    assert deleted == 4  # ids 1-5 fall outside the newest 50, but id 1 is still running
    rows = benchmarks.list_runs(conn, limit=100)
    assert len(rows) == 51
    assert benchmarks.get_run(conn, first) is not None  # a running row is never pruned
    assert rows[0]["id"] > rows[-1]["id"]  # newest first
    conn.close()

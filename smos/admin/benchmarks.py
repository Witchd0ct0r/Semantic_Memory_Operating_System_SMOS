"""Benchmark runs in the admin DB (table `benchmark_runs`). Callers hold `app.state.admin_lock`."""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from smos.admin.db import now_iso

KEEP_RUNS = 50
STATUSES = ("running", "completed", "failed", "cancelled", "interrupted")
_COLUMNS = ("id", "started_at", "finished_at", "status", "preset", "budget_ms", "smos_version",
            "hardware_json", "result_json", "error")


def _row(raw: tuple) -> dict:
    row = dict(zip(_COLUMNS, raw))
    row["hardware"] = json.loads(row.pop("hardware_json")) if row["hardware_json"] else None
    row["result"] = json.loads(row.pop("result_json")) if row["result_json"] else None
    return row


def create_run(conn: sqlite3.Connection, *, preset: str, budget_ms: float, version: str) -> int:
    cursor = conn.execute(
        "INSERT INTO benchmark_runs (started_at, status, preset, budget_ms, smos_version) VALUES (?, 'running', ?, ?, ?)",
        (now_iso(), preset, budget_ms, version))
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, *, status: str, result: Optional[dict],
               hardware: Optional[dict], error: str = "") -> None:
    if status not in STATUSES or status == "running":
        raise ValueError(f"not a final benchmark status: {status}")
    conn.execute(
        "UPDATE benchmark_runs SET finished_at = ?, status = ?, hardware_json = ?, result_json = ?, error = ? WHERE id = ?",
        (now_iso(), status, json.dumps(hardware) if hardware is not None else None,
         json.dumps(result) if result is not None else None, error[:2000], run_id))
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    raw = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM benchmark_runs WHERE id = ?", (run_id,)).fetchone()
    return _row(raw) if raw else None


def list_runs(conn: sqlite3.Connection, limit: int = KEEP_RUNS) -> list[dict]:
    """Newest first, results included (at most KEEP_RUNS rows, each a few tens of kilobytes)."""
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM benchmark_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def previous_completed(conn: sqlite3.Connection, preset: str, before_id: int) -> Optional[dict]:
    raw = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM benchmark_runs "
        "WHERE preset = ? AND status = 'completed' AND id < ? ORDER BY id DESC LIMIT 1",
        (preset, before_id)).fetchone()
    return _row(raw) if raw else None


def has_running(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM benchmark_runs WHERE status = 'running' LIMIT 1").fetchone() is not None


def mark_running_interrupted(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "UPDATE benchmark_runs SET status = 'interrupted', finished_at = ?, error = ? WHERE status = 'running'",
        (now_iso(), "SMOS stopped or restarted while the run was in progress"))
    conn.commit()
    return cursor.rowcount


def prune(conn: sqlite3.Connection, keep: int = KEEP_RUNS) -> int:
    """Delete everything but the newest `keep` runs; a run that is still running is never deleted."""
    cursor = conn.execute(
        "DELETE FROM benchmark_runs WHERE status != 'running' AND id NOT IN "
        "(SELECT id FROM benchmark_runs ORDER BY id DESC LIMIT ?)", (keep,))
    conn.commit()
    return cursor.rowcount

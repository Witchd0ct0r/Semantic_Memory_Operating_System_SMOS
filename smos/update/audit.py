"""The update audit log (admin DB table `update_log`).

Attempts that are refused or fail before the restart are recorded straight away. A successful
update or a rollback is recorded after the restart from `last-result.json`: a rollback restores
the previous admin.db, which never saw the attempt, so the launcher cannot write the row itself.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from smos.admin.db import now_iso
from smos.launcher import UpdatePaths, read_json

_COLUMNS = ("id", "at", "admin_user", "from_version", "to_version", "sha", "outcome", "detail")


def record(conn: sqlite3.Connection, *, admin_user: str, from_version: str, to_version: str, sha: str,
           outcome: str, detail: str = "", at: Optional[str] = None) -> None:
    conn.execute(
        "INSERT INTO update_log (at, admin_user, from_version, to_version, sha, outcome, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (at or now_iso(), admin_user, from_version, to_version, sha, outcome, detail[:2000]))
    conn.commit()


def recent(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM update_log ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


def import_last_result(conn: sqlite3.Connection, paths: UpdatePaths) -> bool:
    """Turn the launcher's last-result.json into one audit row; idempotent."""
    result = read_json(paths.last_result)
    finished = result.get("finished_at") if result else None
    if not result or not isinstance(finished, str) or not finished:
        return False
    if conn.execute("SELECT 1 FROM update_log WHERE at = ?", (finished,)).fetchone():
        return False
    ok = bool(result.get("ok"))
    record(
        conn,
        admin_user=str(result.get("requested_by") or "unknown"),
        from_version=str(result.get("from_version", "")),
        to_version=str(result.get("to_version", "")),
        sha=str(result.get("sha", "")),
        outcome="applied" if ok else "rolled_back",
        detail="" if ok else str(result.get("error", "")),
        at=finished,
    )
    return True

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
_DEFAULT_RANGE = "24h"
_BUCKETS = (60, 300, 900, 3600, 10800, 21600)
_MAX_BUCKETS = 240


def parse_range(value: Optional[str]) -> str:
    return value if value in RANGES else _DEFAULT_RANGE


def bucket_seconds(range_s: int) -> int:
    """The smallest bucket size that keeps a chart at 240 points or fewer."""
    for bucket in _BUCKETS:
        if range_s / bucket <= _MAX_BUCKETS:
            return bucket
    return _BUCKETS[-1]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _epoch_of(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def _rows(conn: sqlite3.Connection, lock, sql: str, params: tuple = ()) -> list[tuple]:
    """One statement under the shared admin lock, never more: token authentication needs the same lock."""
    with lock:
        return conn.execute(sql, params).fetchall()


_BUCKET = "CAST(strftime('%s', ts) AS INTEGER) / ? * ?"


def tiles(conn, lock, since_epoch: float) -> dict:
    calls, errors, avg, active = _rows(
        conn, lock,
        "SELECT COUNT(*), COALESCE(SUM(status >= 400), 0), COALESCE(AVG(latency_ms), 0.0), "
        "COUNT(DISTINCT token_id) FROM usage_log WHERE ts >= ?",
        (_iso(since_epoch),),
    )[0]
    return {
        "calls": calls,
        "errors": errors,
        "error_rate": (errors / calls) if calls else 0.0,
        "avg_latency_ms": float(avg),
        "active_tokens": active,
    }


def load_series(conn, lock, since_epoch: float, until_epoch: float, bucket: int) -> dict:
    rows = _rows(
        conn, lock,
        f"SELECT {_BUCKET} AS b, COALESCE(SUM(status < 400), 0), COALESCE(SUM(status >= 400), 0) "
        "FROM usage_log WHERE ts >= ? AND ts <= ? GROUP BY b",
        (bucket, bucket, _iso(since_epoch), _iso(until_epoch)),
    )
    found = {b: (ok, failed) for b, ok, failed in rows}
    start = int(since_epoch) // bucket * bucket
    end = int(until_epoch) // bucket * bucket
    times = range(start, end + 1, bucket)
    return {
        "ok": [(float(t), float(found.get(t, (0, 0))[0])) for t in times],
        "failed": [(float(t), float(found.get(t, (0, 0))[1])) for t in times],
    }


def latency_series(conn, lock, since_epoch: float, until_epoch: float, bucket: int) -> dict:
    window = f"PARTITION BY {_BUCKET.replace('?', str(int(bucket)))}"
    avg_rows = _rows(
        conn, lock,
        f"SELECT {_BUCKET} AS b, AVG(latency_ms) FROM usage_log WHERE ts >= ? AND ts <= ? GROUP BY b ORDER BY b",
        (bucket, bucket, _iso(since_epoch), _iso(until_epoch)),
    )
    p95_rows = _rows(
        conn, lock,
        f"SELECT b, MIN(latency_ms) FROM (SELECT {_BUCKET} AS b, latency_ms, "
        f"ROW_NUMBER() OVER ({window} ORDER BY latency_ms) AS rn, COUNT(*) OVER ({window}) AS cnt "
        "FROM usage_log WHERE ts >= ? AND ts <= ?) WHERE rn >= 0.95 * cnt GROUP BY b ORDER BY b",
        (bucket, bucket, _iso(since_epoch), _iso(until_epoch)),
    )
    return {
        "avg": [(float(b), float(v)) for b, v in avg_rows],
        "p95": [(float(b), float(v)) for b, v in p95_rows],
    }


def top_calls(conn, lock, since_epoch: float, limit: int = 8) -> list[dict]:
    rows = _rows(
        conn, lock,
        "SELECT method, path, COUNT(*) AS calls, COALESCE(SUM(status >= 400), 0), AVG(latency_ms) "
        "FROM usage_log WHERE ts >= ? GROUP BY method, path ORDER BY calls DESC, path LIMIT ?",
        (_iso(since_epoch), limit),
    )
    return [
        {"method": m, "path": p, "calls": c, "errors": e, "avg_latency_ms": float(a)}
        for m, p, c, e, a in rows
    ]


def token_activity(conn, lock, since_epoch: float) -> list[dict]:
    rows = _rows(
        conn, lock,
        "SELECT t.id, t.name, t.scope, COUNT(u.id) AS calls, COALESCE(SUM(u.status >= 400), 0), MAX(u.ts) "
        "FROM tokens t LEFT JOIN usage_log u ON u.token_id = t.id AND u.ts >= ? "
        "GROUP BY t.id ORDER BY calls DESC, t.id",
        (_iso(since_epoch),),
    )
    return [
        {"token_id": i, "name": n, "scope": s, "calls": c, "errors": e, "last_seen": seen}
        for i, n, s, c, e, seen in rows
    ]


def _write_paths() -> list[str]:
    from smos.api import routes  # imported late: the API package imports this admin package

    return sorted({route.path for route in routes.write.routes})


def _kind(status: int) -> str:
    if status == 401:
        return "auth failure"
    if status == 403:
        return "forbidden"
    if status >= 400:
        return "error"
    return "write"


def recent_events(conn, lock, limit: int = 15) -> list[dict]:
    paths = _write_paths()
    placeholders = ",".join("?" for _ in paths)
    rows = _rows(
        conn, lock,
        "SELECT u.ts, u.method, u.path, u.status, t.name FROM usage_log u "
        "LEFT JOIN tokens t ON t.id = u.token_id "
        f"WHERE u.status >= 400 OR u.path IN ({placeholders}) ORDER BY u.id DESC LIMIT ?",
        (*paths, limit),
    )
    return [
        {"ts": ts, "kind": _kind(status), "method": method, "path": path, "status": status, "token": name}
        for ts, method, path, status, name in rows
    ]


def latest_sample(conn, lock) -> Optional[dict]:
    with lock:
        cursor = conn.execute("SELECT * FROM samples ORDER BY ts DESC LIMIT 1")
        row = cursor.fetchone()
        names = [d[0] for d in cursor.description]
    return dict(zip(names, row)) if row else None


def _samples(conn, lock, since_epoch: float, until_epoch: float) -> list[tuple]:
    return _rows(
        conn, lock,
        "SELECT ts, cpu_percent, cpu_cores, mem_used, mem_limit, disk_used, disk_total, "
        "store_db_bytes, index_bytes, admin_db_bytes, documents, segments FROM samples "
        "WHERE ts >= ? AND ts <= ? ORDER BY ts",
        (_iso(since_epoch), _iso(until_epoch)),
    )


def hardware_series(conn, lock, since_epoch: float, until_epoch: float) -> dict:
    rows = _samples(conn, lock, since_epoch, until_epoch)
    out = {"cpu": [], "mem_used": [], "disk_used": [], "mem_limit": None, "cpu_cores": None, "disk_total": None}
    for ts, cpu, cores, mem, mem_limit, disk, disk_total, *_rest in rows:
        t = _epoch_of(ts)
        if cpu is not None:
            out["cpu"].append((t, float(cpu)))
        if mem is not None:
            out["mem_used"].append((t, float(mem)))
        if disk is not None:
            out["disk_used"].append((t, float(disk)))
        out["mem_limit"], out["cpu_cores"], out["disk_total"] = mem_limit, cores, disk_total
    return out


def growth_series(conn, lock, since_epoch: float, until_epoch: float) -> dict:
    rows = _samples(conn, lock, since_epoch, until_epoch)
    out: dict = {"documents": [], "segments": [], "db_bytes": []}
    for row in rows:
        t = _epoch_of(row[0])
        store_db, index, admin = (x or 0 for x in row[7:10])
        out["documents"].append((t, float(row[10] or 0)))
        out["segments"].append((t, float(row[11] or 0)))
        out["db_bytes"].append((t, float(store_db + index + admin)))
    return out

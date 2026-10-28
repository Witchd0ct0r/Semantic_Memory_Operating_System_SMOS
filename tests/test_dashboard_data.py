from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smos.admin import dashboard_data as dd
from smos.admin import tokens, usage
from smos.admin.db import open_admin_db

BASE = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _epoch(minutes: float) -> float:
    return (BASE + timedelta(minutes=minutes)).timestamp()


class CountingLock:
    """Records the deepest nesting, so tests can prove the lock is only held around single statements."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.depth = 0
        self.max_depth = 0
        self.acquisitions = 0

    def __enter__(self):
        self._lock.acquire()
        self.depth += 1
        self.acquisitions += 1
        self.max_depth = max(self.max_depth, self.depth)

    def __exit__(self, *exc):
        self.depth -= 1
        self._lock.release()


@pytest.fixture
def env(tmp_path: Path):
    conn = open_admin_db(tmp_path)
    lock = CountingLock()
    yield conn, lock
    conn.close()


def _call(conn, minutes, *, status=200, path="/api/v1/query", method="POST", latency=10.0, token_id=None) -> None:
    usage.record(conn, token_id=token_id, method=method, path=path, status=status,
                 latency_ms=latency, req_bytes=100, resp_bytes=200, ts=_ts(minutes))


def test_parse_range_defaults_to_24h() -> None:
    assert dd.parse_range(None) == "24h"
    assert dd.parse_range("bogus") == "24h"
    assert dd.parse_range("") == "24h"
    for value in ("1h", "24h", "7d", "30d"):
        assert dd.parse_range(value) == value


@pytest.mark.parametrize("range_s,expected", [(3600, 60), (86400, 900), (604800, 3600), (2592000, 10800)])
def test_bucket_seconds_keeps_at_most_240_buckets(range_s, expected) -> None:
    assert dd.bucket_seconds(range_s) == expected
    assert range_s / dd.bucket_seconds(range_s) <= 240


def test_tiles_count_calls_errors_and_latency(env) -> None:
    conn, lock = env
    tid, _ = tokens.create_token(conn, "agent")
    for minutes, status, latency in [(1, 200, 10), (2, 200, 30), (3, 500, 20), (4, 401, 5)]:
        _call(conn, minutes, status=status, latency=latency, token_id=tid if status == 200 else None)
    tiles = dd.tiles(conn, lock, _epoch(0))
    assert tiles["calls"] == 4 and tiles["errors"] == 2
    assert tiles["error_rate"] == pytest.approx(0.5)
    assert tiles["avg_latency_ms"] == pytest.approx(16.25)
    assert tiles["active_tokens"] == 1


def test_tiles_ignore_rows_before_the_range(env) -> None:
    conn, lock = env
    _call(conn, -500)
    _call(conn, 1)
    assert dd.tiles(conn, lock, _epoch(0))["calls"] == 1


def test_load_series_fills_gaps_with_zero(env) -> None:
    conn, lock = env
    for minutes in (0.1, 0.5, 2.2):  # buckets: minute 0 (x2), minute 2 (x1)
        _call(conn, minutes)
    _call(conn, 2.4, status=500)
    series = dd.load_series(conn, lock, _epoch(0), _epoch(3.9), bucket=60)
    assert [v for _t, v in series["ok"]] == [2, 0, 1, 0]
    assert [v for _t, v in series["failed"]] == [0, 0, 1, 0]
    assert series["ok"][1][0] - series["ok"][0][0] == 60


def test_latency_series_average_and_p95(env) -> None:
    conn, lock = env
    for i in range(1, 21):  # 1..20 ms in the same bucket
        _call(conn, 0.2, latency=float(i))
    series = dd.latency_series(conn, lock, _epoch(0), _epoch(0.9), bucket=60)
    assert series["avg"][0][1] == pytest.approx(10.5)
    assert series["p95"][0][1] == pytest.approx(19.0)


def test_top_calls_group_by_route_and_sort_by_count(env) -> None:
    conn, lock = env
    for _ in range(3):
        _call(conn, 1, path="/api/v1/query", latency=10)
    _call(conn, 1, path="/api/v1/documents", method="POST", status=500, latency=40)
    _call(conn, 1, path="/api/v1/documents", method="POST", latency=20)
    top = dd.top_calls(conn, lock, _epoch(0), limit=5)
    assert [(r["method"], r["path"], r["calls"]) for r in top] == [
        ("POST", "/api/v1/query", 3), ("POST", "/api/v1/documents", 2)]
    assert top[1]["errors"] == 1 and top[1]["avg_latency_ms"] == pytest.approx(30.0)
    assert len(dd.top_calls(conn, lock, _epoch(0), limit=1)) == 1


def test_token_activity_lists_each_token(env) -> None:
    conn, lock = env
    a, _ = tokens.create_token(conn, "agent", scope="read")
    tokens.create_token(conn, "idle", scope="rw")
    _call(conn, 1, token_id=a)
    rows = {r["name"]: r for r in dd.token_activity(conn, lock, _epoch(0))}
    assert rows["agent"]["calls"] == 1 and rows["idle"]["calls"] == 0
    assert rows["agent"]["scope"] == "read" and rows["idle"]["scope"] == "rw"


def test_recent_events_show_errors_and_writes_newest_first(env) -> None:
    conn, lock = env
    tid, _ = tokens.create_token(conn, "parser")
    _call(conn, 1, path="/api/v1/query")                              # plain read: not an event
    _call(conn, 2, path="/api/v1/documents", token_id=tid)            # write
    _call(conn, 3, path="/api/v1/query", status=401)                  # auth failure
    _call(conn, 4, path="/api/v1/links", status=403, token_id=tid)    # forbidden
    _call(conn, 5, path="/api/v1/recall", status=500, token_id=tid)   # error
    events = dd.recent_events(conn, lock, limit=10)
    assert [e["kind"] for e in events] == ["error", "forbidden", "auth failure", "write"]
    assert events[0]["token"] == "parser" and events[2]["token"] is None
    assert len(dd.recent_events(conn, lock, limit=2)) == 2


def _sample(conn, minutes, **kw) -> None:
    values = dict(cpu_percent=10.0, cpu_cores=2.0, mem_used=100, mem_limit=1000, disk_used=5, disk_total=50,
                  store_db_bytes=1000, index_bytes=500, admin_db_bytes=100, documents=3, segments=9)
    values.update(kw)
    conn.execute(
        "INSERT INTO samples (ts, cpu_percent, cpu_cores, mem_used, mem_limit, disk_used, disk_total, "
        "store_db_bytes, index_bytes, admin_db_bytes, documents, segments) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (_ts(minutes), values["cpu_percent"], values["cpu_cores"], values["mem_used"], values["mem_limit"],
         values["disk_used"], values["disk_total"], values["store_db_bytes"], values["index_bytes"],
         values["admin_db_bytes"], values["documents"], values["segments"]))
    conn.commit()


def test_hardware_and_growth_series_and_latest_sample(env) -> None:
    conn, lock = env
    _sample(conn, 0, cpu_percent=5.0, mem_used=100, documents=1, segments=2)
    _sample(conn, 1, cpu_percent=15.0, mem_used=300, documents=2, segments=5, store_db_bytes=2000)
    hw = dd.hardware_series(conn, lock, _epoch(-1), _epoch(2))
    assert [v for _t, v in hw["cpu"]] == [5.0, 15.0]
    assert [v for _t, v in hw["mem_used"]] == [100, 300]
    assert hw["mem_limit"] == 1000 and hw["cpu_cores"] == 2.0 and hw["disk_total"] == 50
    growth = dd.growth_series(conn, lock, _epoch(-1), _epoch(2))
    assert [v for _t, v in growth["documents"]] == [1, 2]
    assert [v for _t, v in growth["segments"]] == [2, 5]
    assert [v for _t, v in growth["db_bytes"]] == [1000 + 500 + 100, 2000 + 500 + 100]
    latest = dd.latest_sample(conn, lock)
    assert latest["documents"] == 2 and latest["mem_used"] == 300


def test_cpu_series_skips_missing_readings(env) -> None:
    conn, lock = env
    _sample(conn, 0, cpu_percent=None)
    _sample(conn, 1, cpu_percent=7.0)
    assert [v for _t, v in dd.hardware_series(conn, lock, _epoch(-1), _epoch(2))["cpu"]] == [7.0]


def test_empty_database_gives_zeros_and_empty_lists(env) -> None:
    conn, lock = env
    since, until = _epoch(-60), _epoch(0)
    tiles = dd.tiles(conn, lock, since)
    assert tiles["calls"] == 0 and tiles["errors"] == 0 and tiles["error_rate"] == 0.0
    assert tiles["avg_latency_ms"] == 0.0 and tiles["active_tokens"] == 0
    assert dd.top_calls(conn, lock, since) == []
    assert dd.recent_events(conn, lock) == []
    assert dd.token_activity(conn, lock, since) == []
    assert all(v == 0 for _t, v in dd.load_series(conn, lock, since, until, 60)["ok"])
    assert dd.latency_series(conn, lock, since, until, 60) == {"avg": [], "p95": []}
    assert dd.hardware_series(conn, lock, since, until)["cpu"] == []
    assert dd.growth_series(conn, lock, since, until)["documents"] == []
    assert dd.latest_sample(conn, lock) is None


def test_the_lock_is_only_held_around_single_statements(env) -> None:
    conn, lock = env
    _call(conn, 1)
    _sample(conn, 1)
    since, until = _epoch(0), _epoch(5)
    dd.tiles(conn, lock, since)
    dd.load_series(conn, lock, since, until, 60)
    dd.latency_series(conn, lock, since, until, 60)
    dd.top_calls(conn, lock, since)
    dd.token_activity(conn, lock, since)
    dd.recent_events(conn, lock)
    dd.hardware_series(conn, lock, since, until)
    dd.growth_series(conn, lock, since, until)
    dd.latest_sample(conn, lock)
    assert lock.max_depth == 1 and lock.depth == 0
    assert lock.acquisitions >= 9


def test_usage_log_stores_the_route_template_not_the_concrete_id(api) -> None:
    client, headers, app = api
    client.get("/api/v1/nodes/some-concrete-id/text", headers=headers)
    client.delete("/api/v1/memories/another-concrete-id", headers=headers)
    paths = [r["path"] for r in usage.recent(app.state.admin_db, token_id=None, limit=5)]
    assert "/api/v1/nodes/{node_id}/text" in paths
    assert "/api/v1/memories/{id}" in paths
    assert not any("concrete-id" in p for p in paths)

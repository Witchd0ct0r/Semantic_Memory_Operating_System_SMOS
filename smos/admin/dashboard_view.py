from __future__ import annotations

import time
from pathlib import Path

from smos import __version__
from smos.admin import dashboard_data as dd
from smos.admin.charts import Series, format_bytes, line_chart
from smos.update.service import describe as describe_update

_BUCKET_LABELS = {60: "minute", 300: "5 minutes", 900: "15 minutes", 3600: "hour", 10800: "3 hours", 21600: "6 hours"}


def _admin_db_bytes(data_dir: Path) -> int:
    base = Path(data_dir) / "admin.db"
    return sum(p.stat().st_size for p in (base, base.with_name("admin.db-wal")) if p.exists())


def _tile(key: str, label: str, value, hint: str = "") -> dict:
    return {"key": key, "label": label, "value": value, "hint": hint}


def build_dashboard(app, range_key: str) -> dict:
    """Everything the dashboard template needs. Each query takes the shared admin lock only
    around its own single statement (see dashboard_data)."""
    state = app.state
    conn, lock = state.admin_db, state.admin_lock
    range_s = dd.RANGES[range_key]
    until = time.time()
    since = until - range_s
    bucket = dd.bucket_seconds(range_s)
    unit = _BUCKET_LABELS.get(bucket, f"{bucket}s")

    totals = dd.tiles(conn, lock, since)
    stats = state.store.stats()
    sizes = state.store.file_sizes()
    db_bytes = sizes["store_db"] + sizes["index"] + _admin_db_bytes(state.data_dir)
    latest = dd.latest_sample(conn, lock) or {}

    load = dd.load_series(conn, lock, since, until, bucket)
    latency = dd.latency_series(conn, lock, since, until, bucket)
    hardware = dd.hardware_series(conn, lock, since, until)
    growth = dd.growth_series(conn, lock, since, until)

    def allocated(key: str, formatter):
        value = latest.get(key)
        return formatter(value) if value is not None else "n/a"

    tiles = [
        _tile("documents", "Documents", stats["documents"]),
        _tile("segments", "Segments", stats["segments"], "stored items"),
        _tile("db_size", "Database size", format_bytes(db_bytes), "databases + search index"),
        _tile("calls", "API calls", totals["calls"], f"last {range_key}"),
        _tile("error_rate", "Error rate", f"{totals['error_rate'] * 100:.1f}%", f"{totals['errors']} failed"),
        _tile("avg_latency", "Avg latency", f"{totals['avg_latency_ms']:.0f} ms"),
    ]
    allocation = [
        _tile("cpu_cores", "CPU cores allocated", allocated("cpu_cores", lambda v: f"{v:g}")),
        _tile("mem_limit", "Memory limit", allocated("mem_limit", format_bytes)),
        _tile("disk_total", "Data volume size", allocated("disk_total", format_bytes)),
    ]

    mem_limit, disk_total = hardware["mem_limit"], hardware["disk_total"]
    charts = {
        "load": line_chart(
            [Series("successful", load["ok"], "s0"), Series("failed", load["failed"], "s1")],
            title=f"API calls per {unit}"),
        "latency": line_chart(
            [Series("average", latency["avg"], "s0"), Series("95th percentile", latency["p95"], "s1")],
            title="Latency", y_unit="ms"),
        "growth": line_chart(
            [Series("documents", growth["documents"], "s0"), Series("segments", growth["segments"], "s2")],
            title="Stored documents and segments"),
        "db_size": line_chart([Series("size", growth["db_bytes"], "s3")], title="Database size", y_unit="bytes"),
        "cpu": line_chart([Series("CPU", hardware["cpu"], "s0")], title="CPU (% of allocated cores)",
                          y_unit="%", y_max=100),
        "memory": line_chart(
            [Series("used", hardware["mem_used"], "s2")], title="Memory", y_unit="bytes",
            reference=("limit", float(mem_limit)) if mem_limit else None),
        "disk": line_chart(
            [Series("used", hardware["disk_used"], "s3")], title="Disk (data volume)", y_unit="bytes",
            reference=("volume size", float(disk_total)) if disk_total else None),
    }

    return {
        "range_key": range_key,
        "ranges": list(dd.RANGES),
        "version": __version__,
        "update": describe_update(app),
        "tiles": tiles,
        "allocation": allocation,
        "charts": charts,
        "top_calls": dd.top_calls(conn, lock, since),
        "token_activity": dd.token_activity(conn, lock, since),
        "events": dd.recent_events(conn, lock),
    }

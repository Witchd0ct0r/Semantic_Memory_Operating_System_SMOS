from __future__ import annotations

import sqlite3
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smos.admin import metrics, usage
from smos.admin.db import ADMIN_MIGRATIONS, open_admin_db
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload
from smos.memory.vector_store import VectorStore
from smos.storage.migrations import run_migrations
from smos.tools.ingest_tools import do_ingest_document_schema

import threading

COLUMNS = {"ts", "cpu_percent", "cpu_cores", "mem_used", "mem_limit", "disk_used", "disk_total",
           "store_db_bytes", "index_bytes", "admin_db_bytes", "documents", "segments"}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cgroup_v2(root: Path, *, mem_current=300_000_000, mem_max="1000000000", cpu_max="200000 100000",
               usage_usec=1_000_000) -> Path:
    _write(root / "memory.current", f"{mem_current}\n")
    _write(root / "memory.max", f"{mem_max}\n")
    _write(root / "cpu.max", f"{cpu_max}\n")
    _write(root / "cpu.stat", f"usage_usec {usage_usec}\nuser_usec 1\nsystem_usec 1\n")
    return root


def test_cgroup_v2_reports_container_limits(tmp_path) -> None:
    root = _cgroup_v2(tmp_path / "cg")
    hw = metrics.read_hardware(root, previous=None, now=100.0)
    assert hw["mem_used"] == 300_000_000
    assert hw["mem_limit"] == 1_000_000_000
    assert hw["cpu_cores"] == pytest.approx(2.0)
    assert hw["cpu_percent"] is None  # needs two readings


def test_cpu_percent_is_of_the_allocated_cores(tmp_path) -> None:
    root = _cgroup_v2(tmp_path / "cg", usage_usec=1_000_000)
    first = metrics.read_hardware(root, previous=None, now=100.0)
    _cgroup_v2(root, usage_usec=3_000_000)  # +2 CPU-seconds over 10 s on 2 cores = 10 %
    second = metrics.read_hardware(root, previous=first["cpu_state"], now=110.0)
    assert second["cpu_percent"] == pytest.approx(10.0, abs=0.01)


def test_unlimited_cgroup_values_fall_back_to_the_host(tmp_path) -> None:
    root = _cgroup_v2(tmp_path / "cg", mem_max="max", cpu_max="max 100000")
    hw = metrics.read_hardware(root, previous=None, now=1.0)
    assert hw["mem_limit"] > hw["mem_used"] > 0
    assert hw["cpu_cores"] >= 1


def test_cgroup_v1_layout(tmp_path) -> None:
    root = tmp_path / "cg1"
    _write(root / "memory" / "memory.usage_in_bytes", "250000000\n")
    _write(root / "memory" / "memory.limit_in_bytes", "500000000\n")
    _write(root / "cpu" / "cpu.cfs_quota_us", "150000\n")
    _write(root / "cpu" / "cpu.cfs_period_us", "100000\n")
    _write(root / "cpuacct" / "cpuacct.usage", "2000000000\n")
    hw = metrics.read_hardware(root, previous=None, now=1.0)
    assert hw["mem_used"] == 250_000_000 and hw["mem_limit"] == 500_000_000
    assert hw["cpu_cores"] == pytest.approx(1.5)


def test_cgroup_v1_unlimited_values(tmp_path) -> None:
    root = tmp_path / "cg1"
    _write(root / "memory" / "memory.usage_in_bytes", "250000000\n")
    _write(root / "memory" / "memory.limit_in_bytes", "9223372036854771712\n")
    _write(root / "cpu" / "cpu.cfs_quota_us", "-1\n")
    _write(root / "cpu" / "cpu.cfs_period_us", "100000\n")
    hw = metrics.read_hardware(root, previous=None, now=1.0)
    assert hw["mem_limit"] < 9223372036854771712
    assert hw["cpu_cores"] >= 1


def test_missing_cgroup_falls_back_to_the_host(tmp_path) -> None:
    hw = metrics.read_hardware(tmp_path / "does-not-exist", previous=None, now=1.0)
    assert hw["mem_used"] > 0 and hw["mem_limit"] > 0 and hw["cpu_cores"] >= 1


def _app(tmp_path: Path):
    store = VectorStore(persist_path=tmp_path / "data")
    admin = open_admin_db(tmp_path / "data")
    state = types.SimpleNamespace(store=store, admin_db=admin, admin_lock=threading.RLock(),
                                  data_dir=tmp_path / "data")
    return types.SimpleNamespace(state=state)


def _payload(native_id: str) -> IngestionPayload:
    return IngestionPayload(
        external_handle=ExternalHandle(system="s", native_id=native_id),
        blocks=[ContentBlock(block_type="paragraph", text=f"Requirement {native_id} about emissions.")],
    )


def test_store_stats_count_documents_and_segments_not_placeholders(tmp_path) -> None:
    store = VectorStore(persist_path=tmp_path / "data")
    do_ingest_document_schema(_payload("A"), store)
    do_ingest_document_schema(_payload("B"), store)
    store.graph.add_node("ghost-doc", "document", "ghost", {"placeholder": True})
    stats = store.stats()
    assert stats["documents"] == 2
    assert stats["segments"] == 2
    assert stats["memories"] >= 2


def test_store_file_sizes(tmp_path) -> None:
    store = VectorStore(persist_path=tmp_path / "data")
    do_ingest_document_schema(_payload("A"), store)
    sizes = store.file_sizes()
    assert sizes["store_db"] > 0
    assert sizes["index"] >= 0


def test_sample_once_writes_one_complete_row(tmp_path) -> None:
    app = _app(tmp_path)
    do_ingest_document_schema(_payload("A"), app.state.store)
    sampler = metrics.Sampler(app, interval=60)
    assert sampler.sample_once() is True
    rows = app.state.admin_db.execute("SELECT * FROM samples").fetchall()
    assert len(rows) == 1
    columns = [d[0] for d in app.state.admin_db.execute("SELECT * FROM samples").description]
    assert set(columns) == COLUMNS
    row = dict(zip(columns, rows[0]))
    assert row["documents"] == 1 and row["segments"] == 1
    assert row["mem_used"] > 0 and row["disk_total"] >= row["disk_used"] > 0
    assert row["store_db_bytes"] > 0 and row["admin_db_bytes"] > 0


def test_sample_once_never_raises(tmp_path) -> None:
    app = _app(tmp_path)

    def boom():
        raise RuntimeError("store exploded")

    app.state.store.stats = boom
    assert metrics.Sampler(app, interval=60).sample_once() is False
    assert app.state.admin_db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0


def test_sampler_thread_samples_and_stops(tmp_path) -> None:
    app = _app(tmp_path)
    sampler = metrics.Sampler(app, interval=0.05)
    sampler.start()
    deadline = datetime.now() + timedelta(seconds=5)
    while datetime.now() < deadline:
        if app.state.admin_db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] >= 2:
            break
    sampler.stop()
    assert app.state.admin_db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] >= 2
    assert not sampler.is_running()


def test_migration_3_adds_samples_and_keeps_existing_rows(tmp_path) -> None:
    path = tmp_path / "admin.db"
    old = sqlite3.connect(str(path))
    run_migrations(old, ADMIN_MIGRATIONS[:2], path)
    old.execute("INSERT INTO tokens (name, token_hash, created_at, scope) VALUES ('keep', 'h', 't', 'rw')")
    old.commit()
    old.close()
    upgraded = open_admin_db(tmp_path)
    assert upgraded.execute("SELECT name FROM tokens").fetchone()[0] == "keep"
    assert upgraded.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    assert {r[1] for r in upgraded.execute("PRAGMA table_info(samples)")} == COLUMNS
    upgraded.close()


def test_prune_samples_batch_deletes_only_old_rows_within_limit(tmp_path) -> None:
    conn = open_admin_db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        conn.execute("INSERT INTO samples (ts) VALUES (?)", (f"{old[:-6]}.{i:06d}+00:00",))
    conn.execute("INSERT INTO samples (ts) VALUES (?)", (new,))
    conn.commit()
    assert usage.prune_samples_batch(conn, retention_days=30, limit=3) == 3
    assert usage.prune_samples_batch(conn, retention_days=30, limit=3) == 2
    assert usage.prune_samples_batch(conn, retention_days=30, limit=3) == 0
    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    conn.close()

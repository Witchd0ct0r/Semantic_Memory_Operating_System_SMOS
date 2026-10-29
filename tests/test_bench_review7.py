"""Regression tests for the findings of Codex review 7 (docs/superpowers/review/2026-10-29-7-review.md)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from smos.admin import benchmarks
from smos.admin.benchmark_view import build_report
from smos.bench import httpload
from smos.bench import service as service_module
from smos.bench.corpus import CorpusBuilder, Probe
from smos.bench.service import BenchmarkRefused, safe_run_dir
from smos.launcher import UpdatePaths
from smos.update.apply import ApplyRefused, Updater
from tests.benchdata import make_result
from tests.test_bench_httpload import GOOD, stub  # noqa: F401  (stub is a fixture)
from tests.test_bench_service import _bench_tmp, _run, make  # noqa: F401  (make is a fixture)
from tests.test_bench_view import _run as _view_run


def _junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("junctions exist on Windows only")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this account cannot create junctions")


# ---- finding 1: junctions ------------------------------------------------------------------------------------------

def test_safe_run_dir_rejects_a_junction_as_bench_tmp(tmp_path) -> None:
    outside = tmp_path / "outside"
    (outside / "1").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    _junction(data / "bench-tmp", outside)
    with pytest.raises(ValueError):
        safe_run_dir(data, 1)


def test_safe_run_dir_rejects_bench_tmp_resolving_outside_the_data_dir(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    try:
        os.symlink(outside, data / "bench-tmp", target_is_directory=True)
    except (OSError, NotImplementedError):
        _junction(data / "bench-tmp", outside)
    with pytest.raises(ValueError):
        safe_run_dir(data, 1)


# ---- finding 2: prefix-sibling paths -------------------------------------------------------------------------------

def test_orphan_scan_ignores_a_sibling_directory_that_shares_the_prefix(make) -> None:
    base = _bench_tmp(make.app)
    (base / "7").mkdir(parents=True)
    sibling = make.app.state.data_dir / "bench-tmp-other" / "data"
    sibling.mkdir(parents=True)
    sleeper = "import time; time.sleep(600)"
    server_like = subprocess.Popen([sys.executable, "-c", sleeper, "smos.api.main"],
                                   env=dict(os.environ, SMOS_DATA_DIR=str(sibling)))
    runner_like = subprocess.Popen([sys.executable, "-c", sleeper, "-m", "smos.bench", "--work-dir",
                                    str(make.app.state.data_dir / "bench-tmp-other" / "1")])
    try:
        make.build("ok").recover_on_startup()
        assert server_like.poll() is None and runner_like.poll() is None
    finally:
        for proc in (server_like, runner_like):
            proc.kill()
            proc.wait(timeout=10)


# ---- finding 3: update/benchmark check-then-set race ---------------------------------------------------------------

def test_update_and_benchmark_cannot_both_pass_their_checks(make) -> None:
    app = make.app
    updater = Updater(app, SimpleNamespace(enabled=True), None, None, UpdatePaths(app.state.data_dir),
                      running_version="0.2.0")
    app.state.update = SimpleNamespace(updater=updater)

    class PausedLock:
        """The real busy lock, but the updater stops inside its check-then-set window until released."""

        def __init__(self) -> None:
            self.real, self.entered, self.go = threading.Lock(), threading.Event(), threading.Event()

        def acquire(self, blocking: bool = True) -> bool:
            self.entered.set()
            self.go.wait(5)
            return self.real.acquire(blocking)

        def release(self) -> None:
            self.real.release()

    paused = PausedLock()
    updater._busy = paused
    bench_done = threading.Event()

    def check_ready(*_args):
        bench_done.wait(10)  # keep the update in "staging" until the benchmark has made its decision
        raise ApplyRefused("stop here")

    updater._check_ready = check_ready
    outcome: dict = {}

    def begin() -> None:
        try:
            updater.begin("alice", "0.3.0", "a" * 40)
        except ApplyRefused as err:
            outcome["update"] = str(err)

    update_thread = threading.Thread(target=begin)
    update_thread.start()
    assert paused.entered.wait(5)

    svc = make.build("ok")

    def start() -> None:
        try:
            svc.start("bob", "quick", 200.0)
            outcome["benchmark"] = "started"
        except BenchmarkRefused as err:
            outcome["benchmark"] = str(err)

    bench_thread = threading.Thread(target=start)
    bench_thread.start()
    time.sleep(0.3)           # the benchmark's checks now run, or wait on the shared interlock
    paused.go.set()
    bench_thread.join(30)
    bench_done.set()
    update_thread.join(10)
    assert outcome["update"] == "stop here"   # the update got past the benchmark check first...
    assert outcome["benchmark"] != "started"  # ...so the benchmark must have seen it and refused
    svc.cancel()
    svc.wait_idle(20)


# ---- finding 4: supervisor thread cannot start ---------------------------------------------------------------------

def test_supervisor_thread_failure_kills_runner_and_clears_everything(make, monkeypatch) -> None:
    class Boom:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("cannot start a new thread")

    monkeypatch.setattr(service_module, "threading",
                        SimpleNamespace(Thread=Boom, RLock=threading.RLock, Event=threading.Event))
    svc = make.build("sleep")
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    with pytest.raises(BenchmarkRefused):
        svc.start("alice", "quick", 200.0)
    monkeypatch.undo()
    assert make.app.state.benchmark_active is False and svc.status()["state"] == "idle"
    assert not (_bench_tmp(make.app) / "1").exists()
    time.sleep(0.3)
    assert {p.pid for p in psutil.Process().children(recursive=True)} <= before  # no runner left behind
    with make.app.state.admin_lock:
        rows = benchmarks.list_runs(make.app.state.admin_db)
    assert rows and rows[0]["status"] == "failed"
    assert svc.start("alice", "quick", 200.0)  # and the next start works
    svc.cancel()
    assert svc.wait_idle(20)


# ---- finding 5: failed result write --------------------------------------------------------------------------------

def test_failed_result_write_still_leaves_a_final_row(make, monkeypatch) -> None:
    real = benchmarks.finish_run
    calls = {"n": 0}

    def flaky(conn, run_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database or disk is full")
        return real(conn, run_id, **kwargs)

    monkeypatch.setattr(benchmarks, "finish_run", flaky)
    svc = make.build("ok")
    run_id = svc.start("alice", "quick", 200.0)
    assert svc.wait_idle(30)
    monkeypatch.undo()
    row = _run(make.app, run_id)
    assert row["status"] == "failed" and "could not be stored" in row["error"]
    assert svc.start("alice", "quick", 200.0)  # nothing left in the running state to block the next run
    assert svc.wait_idle(30)


# ---- finding 6: mixed-load errors ----------------------------------------------------------------------------------

def test_mixed_step_reports_read_errors_and_client_limit_for_both_phases(stub) -> None:  # noqa: F811
    result = httpload.mixed_step(stub, "tok", clients=2, seconds=0.4,
                                 probes=[GOOD, Probe("FAIL this request please")], builder=CorpusBuilder(1))
    for key in ("read_errors_alone", "read_errors_with_writer", "read_client_limited_alone",
                "read_client_limited_with_writer"):
        assert key in result, key
    assert result["read_errors_alone"] > 0 and result["read_errors_with_writer"] > 0


def test_mixed_chart_table_shows_read_errors_and_labels_p95_as_successful_reads() -> None:
    result = make_result()
    result["mixed"].update({"read_errors_alone": 0, "read_errors_with_writer": 7})
    chart = next(c for c in build_report(_view_run(result), None)["charts"] if c["key"] == "mixed")
    assert "Read errors" in chart["table"] and ">7<" in chart["table"]
    assert "successful" in chart["definition"].lower()


# ---- finding 7: what the memory figure measures --------------------------------------------------------------------

def test_resources_are_named_for_what_they_measure() -> None:
    from smos.bench import runner

    assert runner.RSS_KEY == "largest_post_probe_rss_bytes"
    result = make_result()
    result["resources"] = {runner.RSS_KEY: 600_000_000, "final_disk_bytes": 1}
    details = build_report(_view_run(result), None)["details"]
    assert runner.RSS_KEY in details["resources"] and "peak_server_rss_bytes" not in details["resources"]


# ---- finding 8: model copies ---------------------------------------------------------------------------------------

def test_estimate_covers_two_model_copies() -> None:
    from smos.bench.presets import PRESETS
    from smos.bench.service import requirements

    _disk, ram = requirements(PRESETS["quick"])
    assert ram >= int(2.0 * 1024 ** 3)  # the runner's model and the benchmark server's model


def test_runner_releases_its_model_before_the_server_starts() -> None:
    from smos.bench import runner
    from smos.memory import embeddings

    embeddings.embed("load the model so there is something to release")
    assert embeddings._get_model.cache_info().currsize == 1
    runner.release_model()
    assert embeddings._get_model.cache_info().currsize == 0


def test_the_model_is_loaded_before_ingest_timing_starts(tmp_path) -> None:
    from smos.bench import runner
    from smos.memory import embeddings

    runner.release_model()
    store = runner._open_and_warm(tmp_path / "data")
    try:
        assert embeddings._get_model.cache_info().currsize == 1  # loaded outside the timed ingest
    finally:
        store.close()
        runner.release_model()

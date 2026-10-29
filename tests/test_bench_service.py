from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from smos.admin import benchmarks
from smos.bench import service as service_module
from smos.bench.service import BenchmarkRefused, BenchmarkService, safe_run_dir
from smos.update.apply import ApplyRefused, Updater
from smos.launcher import UpdatePaths

FAKE = Path(__file__).parent / "fake_bench_runner.py"


def _factory(mode: str):
    def make(work_dir: Path, preset: str, budget_ms: float) -> list[str]:
        return [sys.executable, str(FAKE), "--mode", mode, "--work-dir", str(work_dir), "--preset", preset,
                "--budget-ms", str(budget_ms)]
    return make


@pytest.fixture
def make(api):
    client, headers, app = api
    created = []

    def build(mode: str = "ok", **kwargs) -> BenchmarkService:
        kwargs.setdefault("poll_interval", 0.05)
        svc = BenchmarkService(app, app.state.data_dir, command_factory=_factory(mode), **kwargs)
        created.append(svc)
        return svc

    yield SimpleNamespace(build=build, client=client, headers=headers, app=app)
    for svc in created:
        svc.cancel()
        svc.wait_idle(20)


def _run(app, run_id: int) -> dict:
    with app.state.admin_lock:
        return benchmarks.get_run(app.state.admin_db, run_id)


def _wait_for(path: Path, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while not path.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert path.exists(), f"{path} never appeared"


def _bench_tmp(app) -> Path:
    return app.state.data_dir / "bench-tmp"


def test_start_runs_and_stores_completed_result(make) -> None:
    svc = make.build("ok")
    run_id = svc.start("alice", "quick", 200.0)
    assert make.app.state.benchmark_active is True
    assert make.client.post("/api/v1/query", headers=make.headers, json={"query": "during the run", "k": 1}).status_code == 503
    assert svc.wait_idle(30)
    row = _run(make.app, run_id)
    assert row["status"] == "completed" and row["preset"] == "quick" and row["result"]["schema"] == 1
    assert row["result"]["cleanup"] == "verified"
    assert not (_bench_tmp(make.app) / str(run_id)).exists()
    assert make.app.state.benchmark_active is False
    assert make.client.post("/api/v1/query", headers=make.headers, json={"query": "after the run", "k": 1}).status_code == 200
    assert svc.status()["state"] == "idle"


def test_second_start_is_refused_while_running(make) -> None:
    svc = make.build("sleep")
    run_id = svc.start("alice", "quick", 200.0)
    with pytest.raises(BenchmarkRefused):
        svc.start("bob", "quick", 200.0)
    other = make.build("ok")
    with pytest.raises(BenchmarkRefused):  # a second service object (second tab/worker) sees the stored running row
        other.start("bob", "quick", 200.0)
    assert _run(make.app, run_id)["status"] == "running" and make.app.state.benchmark_active is True
    svc.cancel()
    assert svc.wait_idle(20)


def test_start_refused_when_update_in_progress(make) -> None:
    svc = make.build("ok")
    make.app.state.maintenance = True
    with pytest.raises(BenchmarkRefused):
        svc.start("alice", "quick", 200.0)
    make.app.state.maintenance = False
    make.app.state.update = SimpleNamespace(updater=SimpleNamespace(state=lambda: "staging"))
    with pytest.raises(BenchmarkRefused):
        svc.start("alice", "quick", 200.0)
    assert make.app.state.benchmark_active is False


def test_updater_refuses_while_benchmark_active(make) -> None:
    updater = Updater(make.app, SimpleNamespace(enabled=True), None, None, UpdatePaths(make.app.state.data_dir),
                      running_version="0.2.0")
    make.app.state.benchmark_active = True
    try:
        with pytest.raises(ApplyRefused, match="benchmark"):
            updater.begin("alice", "0.3.0", "a" * 40)
    finally:
        make.app.state.benchmark_active = False


def test_start_refused_when_disk_or_ram_low_and_flag_never_set(make) -> None:
    low_disk = make.build("ok", free_disk=lambda path: 1024)
    with pytest.raises(BenchmarkRefused, match="disk"):
        low_disk.start("alice", "quick", 200.0)
    low_ram = make.build("ok", available_ram=lambda: 1024)
    with pytest.raises(BenchmarkRefused, match="memory"):
        low_ram.start("alice", "quick", 200.0)
    assert make.app.state.benchmark_active is False
    assert not _bench_tmp(make.app).exists()
    with make.app.state.admin_lock:
        assert benchmarks.list_runs(make.app.state.admin_db) == []


def test_drain_timeout_clears_flag_and_refuses(make) -> None:
    svc = make.build("ok", drain_timeout=0.3)
    make.app.state.api_inflight = 1
    try:
        with pytest.raises(BenchmarkRefused, match="in progress"):
            svc.start("alice", "quick", 200.0)
    finally:
        make.app.state.api_inflight = 0
    assert make.app.state.benchmark_active is False
    assert not _bench_tmp(make.app).exists()


def test_start_validates_input(make) -> None:
    svc = make.build("ok")
    for bad in (0, -1, 60001, float("nan"), "abc", None):
        with pytest.raises(ValueError):
            svc.start("alice", "quick", bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        svc.start("alice", "nonsense", 200.0)
    with pytest.raises(ValueError):
        svc.start("alice", "smoke", 200.0)  # the smoke preset is not offered
    assert make.app.state.benchmark_active is False


def test_status_reports_progress_while_running(make) -> None:
    svc = make.build("sleep")
    run_id = svc.start("alice", "quick", 200.0)
    _wait_for(_bench_tmp(make.app) / str(run_id) / "record.json")
    status = svc.status()
    assert status["state"] == "running" and status["run_id"] == run_id
    assert status["progress"]["percent"] == 50 and status["preset"] == "quick"
    svc.cancel()
    assert svc.wait_idle(20)


def test_cancel_ends_maintenance_and_removes_directory(make) -> None:
    svc = make.build("sleep")
    run_id = svc.start("alice", "quick", 200.0)
    _wait_for(_bench_tmp(make.app) / str(run_id) / "record.json")
    assert svc.cancel() is True
    assert svc.wait_idle(20)
    assert _run(make.app, run_id)["status"] == "cancelled"
    assert not (_bench_tmp(make.app) / str(run_id)).exists()
    assert make.app.state.benchmark_active is False
    assert svc.cancel() is False  # nothing left to cancel


def test_runner_crash_marks_failed_and_cleans_up(make) -> None:
    svc = make.build("crash")
    run_id = svc.start("alice", "quick", 200.0)
    assert svc.wait_idle(20)
    row = _run(make.app, run_id)
    assert row["status"] == "failed" and "boom" in row["error"]
    assert not (_bench_tmp(make.app) / str(run_id)).exists() and make.app.state.benchmark_active is False


def test_runner_killed_externally_cleans_up(make) -> None:
    svc = make.build("die")
    run_id = svc.start("alice", "quick", 200.0)
    assert svc.wait_idle(20)
    row = _run(make.app, run_id)
    assert row["status"] == "failed" and "9" in row["error"]
    assert not (_bench_tmp(make.app) / str(run_id)).exists() and make.app.state.benchmark_active is False


def test_cleanup_kills_grandchild_processes(make) -> None:
    svc = make.build("sleep")
    run_id = svc.start("alice", "quick", 200.0)
    pid_file = _bench_tmp(make.app) / str(run_id) / "grandchild.pid"
    _wait_for(pid_file)
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    assert psutil.pid_exists(grandchild)
    svc.cancel()
    assert svc.wait_idle(20)
    assert not psutil.pid_exists(grandchild)


def test_cleanup_failure_is_recorded_not_silent(make, monkeypatch) -> None:
    def broken(*_args, **_kwargs):
        raise OSError("disk says no")

    monkeypatch.setattr(service_module, "_rmtree", broken)
    svc = make.build("ok")
    run_id = svc.start("alice", "quick", 200.0)
    assert svc.wait_idle(30)
    row = _run(make.app, run_id)
    assert row["status"] == "completed"
    assert row["result"]["cleanup"] != "verified" and str(run_id) in row["result"]["cleanup"]
    assert make.app.state.benchmark_active is False  # the API is back even though files remain
    monkeypatch.undo()
    svc.recover_on_startup()  # the next start-up removes what was left
    assert not (_bench_tmp(make.app) / str(run_id)).exists()


def test_wall_time_cap_fails_run_and_cleans_up(make) -> None:
    svc = make.build("hang", wall_cap=lambda preset: 0.6)
    run_id = svc.start("alice", "quick", 200.0)
    assert svc.wait_idle(30)
    row = _run(make.app, run_id)
    assert row["status"] == "failed" and "smaller preset" in row["error"]
    assert not (_bench_tmp(make.app) / str(run_id)).exists()


def test_recover_on_startup_marks_interrupted_and_removes_stale_dirs(make) -> None:
    app = make.app
    with app.state.admin_lock:
        stuck = benchmarks.create_run(app.state.admin_db, preset="quick", budget_ms=200.0, version="0.3.3")
    stale = _bench_tmp(app) / str(stuck)
    (stale / "data").mkdir(parents=True)
    (stale / "data" / "faiss.index").write_bytes(b"x")
    make.build("ok").recover_on_startup()
    assert _run(app, stuck)["status"] == "interrupted" and not stale.exists()
    assert app.state.benchmark_active is False


def test_recover_leaves_foreign_entries_alone(make) -> None:
    base = _bench_tmp(make.app)
    base.mkdir(parents=True)
    (base / "notes.txt").write_text("keep", encoding="utf-8")
    (base / "not-a-number").mkdir()
    (base / "12").mkdir()
    make.build("ok").recover_on_startup()
    assert (base / "notes.txt").exists() and (base / "not-a-number").exists()
    assert not (base / "12").exists()


def test_safe_run_dir_rejects_traversal_and_non_int(tmp_path) -> None:
    ok = safe_run_dir(tmp_path, 7)
    assert ok == tmp_path / "bench-tmp" / "7"
    for bad in ("../x", "1/../../data", "7", 0, -3, True, None, 1.5):
        with pytest.raises(ValueError):
            safe_run_dir(tmp_path, bad)  # type: ignore[arg-type]


def test_safe_run_dir_rejects_a_link_pointing_outside(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "bench-tmp").mkdir()
    link = tmp_path / "bench-tmp" / "1"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("this account cannot create symlinks")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if made.returncode != 0:
            pytest.skip("this account cannot create symlinks or junctions")
    with pytest.raises(ValueError):
        safe_run_dir(tmp_path, 1)


def test_launch_secret_in_env_not_in_argv_or_logs(make, monkeypatch, caplog) -> None:
    monkeypatch.setattr(service_module.secrets, "token_urlsafe", lambda n=32: "KNOWN-SECRET-VALUE")
    svc = make.build("sleep")
    with caplog.at_level("DEBUG"):
        run_id = svc.start("alice", "quick", 200.0)
        run_dir = _bench_tmp(make.app) / str(run_id)
        _wait_for(run_dir / "record.json")
        record = json.loads((run_dir / "record.json").read_text(encoding="utf-8"))
        launch_hash = (run_dir / "launch.sha256").read_text(encoding="utf-8").strip()
        svc.cancel()
        assert svc.wait_idle(20)
    assert record["has_secret"] is True and record["secret_sha256"] == launch_hash
    assert launch_hash == hashlib.sha256(b"KNOWN-SECRET-VALUE").hexdigest()
    assert not any("KNOWN-SECRET-VALUE" in a for a in record["argv"])
    assert "KNOWN-SECRET-VALUE" not in caplog.text


def test_recover_kills_orphan_processes_of_a_dead_run(make) -> None:
    """A hard kill of SMOS leaves the runner (a separate process) behind: the next start-up ends it."""
    base = _bench_tmp(make.app)
    (base / "7" / "data").mkdir(parents=True)
    sleeper = "import time; time.sleep(600)"
    runner_like = subprocess.Popen([sys.executable, "-c", sleeper, "-m", "smos.bench", "--work-dir", str(base / "7")])
    mentions_only = subprocess.Popen([sys.executable, "-c", sleeper, str(base / "7" / "data")])  # not a benchmark process
    mentions_data_dir = subprocess.Popen([sys.executable, "-c", sleeper, str(make.app.state.data_dir)])
    try:
        make.build("ok").recover_on_startup()
        assert runner_like.wait(timeout=10) is not None  # ended by the recovery
        assert mentions_only.poll() is None              # merely naming the path is not enough
        assert mentions_data_dir.poll() is None
    finally:
        for proc in (runner_like, mentions_only, mentions_data_dir):
            proc.kill()
            proc.wait(timeout=10)


def test_orphan_server_is_recognised_by_its_data_directory_not_its_command_line(make) -> None:
    base = _bench_tmp(make.app)
    (base / "8" / "data").mkdir(parents=True)
    env = dict(os.environ, SMOS_DATA_DIR=str(base / "8" / "data"))
    server_like = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", "smos.api.main"], env=env)
    other_env = dict(os.environ, SMOS_DATA_DIR=str(make.app.state.data_dir))
    live_like = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", "smos.api.main"], env=other_env)
    try:
        make.build("ok").recover_on_startup()
        assert server_like.wait(timeout=10) is not None
        assert live_like.poll() is None  # the real server (its own data dir) is never touched
    finally:
        for proc in (server_like, live_like):
            proc.kill()
            proc.wait(timeout=10)

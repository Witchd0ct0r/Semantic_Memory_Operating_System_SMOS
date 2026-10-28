from __future__ import annotations

import http.server
import json
import sys
import threading
from pathlib import Path

from smos import launcher
from smos.launcher import EXIT_RESTART, Supervisor, UpdatePaths

BUILD = "build-1"


class FakeChild:
    """poll() returns None until the `polls_until_exit`-th call, then `code`."""

    def __init__(self, polls_until_exit: int | None = None, code: int = 0, log=("boom",)) -> None:
        self.polls_until_exit, self.code, self._log = polls_until_exit, code, list(log)
        self.polls, self.terminated, self.killed = 0, False, False

    def poll(self):
        self.polls += 1
        if self.terminated:
            return self.code
        if self.polls_until_exit is not None and self.polls >= self.polls_until_exit:
            return self.code
        return None

    def wait(self, timeout=None) -> int:
        return self.code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def tail(self) -> list[str]:
        return self._log


class Harness:
    """Scripted spawn/health with a fake clock; records what was spawned."""

    def __init__(self, data: Path, children=None, healthy_versions=("0.3.0",)) -> None:
        self.data, self.now = data, 0.0
        self.spawned: list[Path | None] = []
        self.children = list(children or [])
        self.healthy_versions = set(healthy_versions)
        self.reported = "0.3.0"  # what the (fake) running code says its version is
        self.on_spawn = None
        self.health_calls: list = []

    def spawn(self, overlay):
        self.spawned.append(overlay)
        if self.on_spawn:
            self.on_spawn(overlay)
        return self.children.pop(0) if self.children else FakeChild(polls_until_exit=3)

    def health(self, expected):
        self.health_calls.append(expected)
        if expected is None:
            return True
        return self.reported == expected and expected in self.healthy_versions

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def supervisor(self, **kw) -> Supervisor:
        return Supervisor(self.data, spawn=self.spawn, health=self.health, build_id=BUILD,
                          clock=self.clock, sleep=self.sleep, health_timeout=5.0, poll_interval=1.0, **kw)


def _data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (data / "metadata.db").write_text("original")
    return data


def _stage(data: Path, version: str = "0.3.0") -> UpdatePaths:
    paths = UpdatePaths(data)
    staging = paths.staging_dir(version)
    (staging / "smos").mkdir(parents=True)
    (staging / "smos" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    launcher.write_json_atomic(paths.pending, {
        "version": version, "sha": "a" * 40, "requested_by": "admin",
        "requested_at": "2026-10-28T10:00:00+00:00", "from_version": "0.2.0"})
    return paths


def test_plain_start_passes_no_overlay_and_returns_exit_code(tmp_path) -> None:
    h = Harness(_data(tmp_path), children=[FakeChild(polls_until_exit=2, code=7)])
    assert h.supervisor().run() == 7
    assert h.spawned == [None]


def test_exit_75_restarts_then_returns_next_exit_code(tmp_path) -> None:
    h = Harness(_data(tmp_path), children=[FakeChild(1, EXIT_RESTART), FakeChild(1, 0)])
    assert h.supervisor().run() == 0
    assert len(h.spawned) == 2


def test_stale_overlay_is_ignored_on_start(tmp_path) -> None:
    data = _data(tmp_path)
    paths = UpdatePaths(data)
    (paths.overlay_dir("0.3.0") / "smos").mkdir(parents=True)
    launcher.write_json_atomic(paths.current, {"version": "0.3.0", "base_build_id": "older-image"})
    h = Harness(data, children=[FakeChild(1, 0)])
    h.supervisor().run()
    assert h.spawned == [None] and not paths.overlay_dir("0.3.0").exists()


def test_pending_update_success(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data)
    h = Harness(data, children=[FakeChild(polls_until_exit=4, code=0)])
    assert h.supervisor().run() == 0
    assert h.spawned == [paths.overlay_dir("0.3.0")]
    current = launcher.read_json(paths.current)
    assert current["version"] == "0.3.0" and current["sha"] == "a" * 40 and current["base_build_id"] == BUILD
    result = launcher.read_json(paths.last_result)
    assert result["ok"] is True and result["to_version"] == "0.3.0" and result["from_version"] == "0.2.0"
    assert result["requested_by"] == "admin" and result["sha"] == "a" * 40
    assert not paths.pending.exists() and not paths.staging_dir("0.3.0").exists()
    assert launcher.snapshot_matches(paths, "0.3.0@2026-10-28T10:00:00+00:00")
    assert h.health_calls[0] == "0.3.0"


def _migrating(h: Harness, data: Path) -> None:
    def on_spawn(overlay):
        if overlay is not None:
            (data / "metadata.db").write_text("migrated")
            (data / "new.db").write_text("new")
    h.on_spawn = on_spawn


def test_unhealthy_new_code_rolls_back_code_and_data(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data)
    h = Harness(data, healthy_versions=(), children=[FakeChild(), FakeChild(polls_until_exit=3)])
    _migrating(h, data)
    h.supervisor().run()
    assert (data / "metadata.db").read_text() == "original" and not (data / "new.db").exists()
    assert not paths.current.exists()
    assert h.spawned[0] == paths.overlay_dir("0.3.0") and h.spawned[1] is None
    result = launcher.read_json(paths.last_result)
    assert result["ok"] is False and "boom" in "\n".join(result["log_tail"])
    assert not paths.pending.exists()


def test_wrong_reported_version_rolls_back(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data)
    h = Harness(data, children=[FakeChild(), FakeChild(polls_until_exit=3)])
    h.reported = "0.2.0"  # the new overlay is not what actually runs
    _migrating(h, data)
    h.supervisor().run()
    assert (data / "metadata.db").read_text() == "original"
    assert launcher.read_json(paths.last_result)["ok"] is False


def test_child_exiting_early_rolls_back(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data)
    h = Harness(data, children=[FakeChild(polls_until_exit=1, code=3), FakeChild(polls_until_exit=3)])
    _migrating(h, data)
    h.supervisor().run()
    result = launcher.read_json(paths.last_result)
    assert result["ok"] is False and "exited" in result["error"]
    assert (data / "metadata.db").read_text() == "original"


def test_health_timeout_uses_clock_not_wall_time(tmp_path) -> None:
    data = _data(tmp_path)
    _stage(data)
    h = Harness(data, healthy_versions=(), children=[FakeChild(), FakeChild(polls_until_exit=3)])
    h.supervisor().run()
    assert 5.0 <= h.now < 12.0  # fake seconds advanced by the sleeps, nothing real was waited


def test_rollback_restores_previous_overlay_when_there_was_one(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data, "0.4.0")
    (paths.overlay_dir("0.3.0") / "smos").mkdir(parents=True)
    launcher.write_json_atomic(paths.current, {"version": "0.3.0", "sha": "b" * 40, "base_build_id": BUILD})
    h = Harness(data, healthy_versions=(), children=[FakeChild(), FakeChild(polls_until_exit=3)])
    h.supervisor().run()
    assert launcher.read_json(paths.current)["version"] == "0.3.0"
    assert h.spawned[1] == paths.overlay_dir("0.3.0")
    assert not paths.overlay_dir("0.4.0").exists()


def test_rollback_failure_returns_nonzero_and_keeps_files(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data)
    h = Harness(data, children=[FakeChild(), FakeChild()])
    h.healthy_versions = set()
    h.health = lambda expected: False  # even the restored code never answers
    code = h.supervisor().run()
    assert code != 0
    result = launcher.read_json(paths.last_result)
    assert result["ok"] is False and "rollback" in result["error"].lower()
    assert not paths.pending.exists()  # otherwise every Docker restart would redo the update
    assert (paths.snapshot / "metadata.db").read_text() == "original"


def test_restart_with_pending_and_complete_snapshot_does_not_resnapshot(tmp_path) -> None:
    data = _data(tmp_path)
    paths = _stage(data)
    launcher.snapshot_data(data, paths, "0.3.0@2026-10-28T10:00:00+00:00")  # taken before the crash
    # the crash happened after the overlay was switched and the new code had migrated the data
    paths.overlay_dir("0.3.0").parent.mkdir(parents=True)
    paths.staging_dir("0.3.0").rename(paths.overlay_dir("0.3.0"))
    launcher.write_json_atomic(paths.current, {"version": "0.3.0", "sha": "a" * 40, "base_build_id": BUILD})
    (data / "metadata.db").write_text("migrated")
    h = Harness(data, healthy_versions=(), children=[FakeChild(), FakeChild(polls_until_exit=3)])
    h.supervisor().run()
    assert (data / "metadata.db").read_text() == "original"


def test_pending_without_staging_or_overlay_is_recorded_as_failed_and_start_continues(tmp_path) -> None:
    data = _data(tmp_path)
    paths = UpdatePaths(data)
    launcher.write_json_atomic(paths.pending, {"version": "0.3.0", "requested_at": "t"})
    h = Harness(data, children=[FakeChild(1, 0)])
    assert h.supervisor().run() == 0
    assert h.spawned == [None] and not paths.pending.exists()
    assert launcher.read_json(paths.last_result)["ok"] is False


def test_request_stop_terminates_child_and_run_returns(tmp_path) -> None:
    data = _data(tmp_path)
    child = FakeChild()  # never exits on its own
    h = Harness(data, children=[child])
    sup = h.supervisor()
    original_sleep = h.sleep

    def stopping_sleep(seconds):
        original_sleep(seconds)
        sup.request_stop()

    sup._sleep = stopping_sleep
    sup.run()
    assert child.terminated


def test_process_child_relays_output_and_keeps_tail(tmp_path) -> None:
    child = launcher.ProcessChild(
        [sys.executable, "-c", "print('one'); print('two'); import sys; sys.exit(3)"], env=None)
    assert child.wait(timeout=20) == 3
    child.join_output()
    assert child.tail()[-2:] == ["one", "two"]


def test_http_health_checks_status_and_version() -> None:
    state = {"body": {"status": "ok", "version": "0.3.0"}}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            data = json.dumps(state["body"]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        health = launcher.http_health(server.server_port)
        assert health(None) and health("0.3.0") and not health("0.4.0")
        state["body"] = {"status": "starting"}
        assert not health(None)
    finally:
        server.shutdown()
    assert not launcher.http_health(server.server_port)(None)  # nothing listening any more

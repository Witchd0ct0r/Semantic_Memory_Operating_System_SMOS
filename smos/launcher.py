"""Container supervisor for SMOS and the on-disk state of the self-updater.

Standard library only, on purpose. This file is part of the image and is never replaced by an
update overlay (the supervisor runs before any overlay is on the path), so the formats and
behaviour here are frozen until the image is rebuilt. A release that changes this file is
therefore reported as "needs rebuild" by the updater.
"""
from __future__ import annotations

import collections
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

EXIT_RESTART = 75  # the app exits with this code to ask the supervisor for a restart
_BUILD_INFO_DEFAULT = "/opt/smos/build-info.json"


class UpdatePaths:
    """Where the updater keeps its files, under <data dir>/updates."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "updates"
        self.current = self.root / "current.json"
        self.current_prev = self.root / "current.json.prev"
        self.pending = self.root / "pending.json"
        self.last_result = self.root / "last-result.json"
        self.snapshot = self.root / "snapshot"

    def overlay_dir(self, version: str) -> Path:
        return self.root / "overlay" / version

    def staging_dir(self, version: str) -> Path:
        return self.root / f"staging-{version}"


def read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json_atomic(path: Path, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_build_info() -> dict:
    return read_json(Path(os.environ.get("SMOS_BUILD_INFO", _BUILD_INFO_DEFAULT))) or {}


def active_overlay(paths: UpdatePaths, build_id: Optional[str]) -> Optional[Path]:
    """The code overlay to run, or None for the code baked into the image.

    An overlay made for a different image build (the image was rebuilt since) or whose
    directory is gone is stale: it and its pointer are deleted."""
    current = read_json(paths.current)
    if current is None:
        return None
    version = str(current.get("version", ""))
    overlay = paths.overlay_dir(version) if version else None
    if overlay is not None and overlay.is_dir() and current.get("base_build_id") == build_id:
        return overlay
    if overlay is not None:
        shutil.rmtree(overlay, ignore_errors=True)
    paths.current.unlink(missing_ok=True)
    return None


def _data_entries(data_dir: Path) -> list[Path]:
    return [p for p in Path(data_dir).iterdir() if p.name != "updates"]


def _copy_entry(src: Path, dst_dir: Path) -> None:
    target = dst_dir / src.name
    if src.is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)


def snapshot_data(data_dir: Path, paths: UpdatePaths, token: str) -> None:
    """Replace the snapshot with a copy of everything in the data dir except updates/."""
    shutil.rmtree(paths.snapshot, ignore_errors=True)
    paths.snapshot.mkdir(parents=True)
    for entry in _data_entries(data_dir):
        _copy_entry(entry, paths.snapshot)
    (paths.snapshot / ".complete").write_text(token, encoding="utf-8")


def snapshot_matches(paths: UpdatePaths, token: str) -> bool:
    try:
        return (paths.snapshot / ".complete").read_text(encoding="utf-8") == token
    except OSError:
        return False


def restore_data(data_dir: Path, paths: UpdatePaths) -> None:
    """Put the data dir back exactly as the snapshot has it (updates/ is left alone)."""
    for entry in _data_entries(data_dir):
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in paths.snapshot.iterdir():
        if entry.name != ".complete":
            _copy_entry(entry, Path(data_dir))


# --------------------------------------------------------------------------- supervisor

_TAIL_LINES = 40


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Child(Protocol):
    def poll(self) -> Optional[int]: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def tail(self) -> list[str]: ...


class ProcessChild:
    """A real child process whose output is relayed to ours and whose last lines are kept."""

    def __init__(self, argv: list[str], env: Optional[dict]) -> None:
        self._proc = subprocess.Popen(
            argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._tail: collections.deque = collections.deque(maxlen=_TAIL_LINES)
        self._reader = threading.Thread(target=self._relay, daemon=True, name="child-output")
        self._reader.start()

    def _relay(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip("\n")
            self._tail.append(line)
            print(line, flush=True)

    def join_output(self, timeout: float = 5.0) -> None:
        self._reader.join(timeout)

    def poll(self) -> Optional[int]:
        return self._proc.poll()

    def wait(self, timeout: Optional[float] = None) -> int:
        return self._proc.wait(timeout)

    def terminate(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()

    def tail(self) -> list[str]:
        return list(self._tail)


def http_health(port: int) -> Callable[[Optional[str]], bool]:
    """A health probe: the app answers status ok and, when a version is given, reports exactly it."""
    url = f"http://127.0.0.1:{port}/api/v1/health"

    def check(expected_version: Optional[str]) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=4) as response:
                body = json.loads(response.read(65536).decode("utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(body, dict) or body.get("status") != "ok":
            return False
        return expected_version is None or body.get("version") == expected_version

    return check


class Supervisor:
    """Runs the app as a child, restarts it on exit code 75, and applies a staged update with
    snapshot, health check and rollback."""

    def __init__(
        self,
        data_dir: Path,
        *,
        spawn: Callable[[Optional[Path]], Child],
        health: Callable[[Optional[str]], bool],
        build_id: Optional[str],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        health_timeout: float = 120.0,
        poll_interval: float = 1.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.paths = UpdatePaths(self.data_dir)
        self._spawn, self._health, self.build_id = spawn, health, build_id
        self._clock, self._sleep = clock, sleep
        self._health_timeout, self._poll_interval = health_timeout, poll_interval
        self._stop = False
        self._child: Optional[Child] = None

    def request_stop(self) -> None:
        self._stop = True
        child = self._child
        if child is not None:
            child.terminate()

    def run(self) -> int:
        while True:
            if self.paths.pending.exists():
                child, failure = self._apply_pending()
                if failure is not None:
                    return failure
                if child is None:
                    continue  # the pending file was unusable and is gone; start normally
            else:
                child = self._start(active_overlay(self.paths, self.build_id))
            code = self._wait(child)
            if self._stop or code != EXIT_RESTART:
                return code

    def _start(self, overlay: Optional[Path]) -> Child:
        self._child = self._spawn(overlay)
        return self._child

    def _wait(self, child: Child) -> int:
        while True:
            code = child.poll()
            if code is not None:
                return code
            self._sleep(self._poll_interval)

    def _apply_pending(self) -> tuple[Optional[Child], Optional[int]]:
        """Returns (running child, None), (None, None) when the pending file was unusable, or
        (None, exit code) when even the rollback could not bring the app back."""
        pending = read_json(self.paths.pending) or {}
        version = str(pending.get("version", ""))
        staging = self.paths.staging_dir(version) if version else None
        overlay = self.paths.overlay_dir(version) if version else None
        if not version or not ((staging and staging.is_dir()) or (overlay and overlay.is_dir())):
            self._record(pending, ok=False, error="the staged update files were missing at restart")
            self.paths.pending.unlink(missing_ok=True)
            return None, None

        token = f"{version}@{pending.get('requested_at', '')}"
        if not snapshot_matches(self.paths, token):  # a restart mid-update must not snapshot migrated data
            snapshot_data(self.data_dir, self.paths, token)
        if staging is not None and staging.is_dir():
            shutil.rmtree(overlay, ignore_errors=True)
            overlay.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, overlay)
        current = read_json(self.paths.current)
        if current is not None and current.get("version") != version:
            write_json_atomic(self.paths.current_prev, current)
        elif current is None:
            self.paths.current_prev.unlink(missing_ok=True)
        write_json_atomic(self.paths.current, {
            "version": version, "sha": pending.get("sha", ""),
            "base_build_id": self.build_id, "applied_at": _now_iso()})

        child = self._start(overlay)
        problem = self._wait_healthy(child, version)
        if problem is None:
            self._record(pending, ok=True)
            self.paths.pending.unlink(missing_ok=True)
            self._prune_overlays()
            return child, None
        if self._stop:
            return child, 0

        log_tail = child.tail()
        child.terminate()
        try:
            child.wait(timeout=30)
        except Exception:
            child.kill()
        restore_data(self.data_dir, self.paths)
        previous = read_json(self.paths.current_prev)
        if previous is not None:
            write_json_atomic(self.paths.current, previous)
            self.paths.current_prev.unlink(missing_ok=True)
        else:
            self.paths.current.unlink(missing_ok=True)
        if previous is None or previous.get("version") != version:
            shutil.rmtree(overlay, ignore_errors=True)
        restored = self._start(active_overlay(self.paths, self.build_id))
        recovery = self._wait_healthy(restored, None)
        if recovery is None:
            self._record(pending, ok=False, error=problem, log_tail=log_tail)
            self.paths.pending.unlink(missing_ok=True)
            return restored, None
        restored.terminate()
        self._record(pending, ok=False, log_tail=log_tail,
                     error=f"{problem}; rollback also failed: {recovery}")
        self.paths.pending.unlink(missing_ok=True)  # else every container restart would redo the update
        return None, 1

    def _wait_healthy(self, child: Child, version: Optional[str]) -> Optional[str]:
        """None when healthy (and reporting `version`), else a short reason."""
        started = self._clock()
        while True:
            if self._stop:
                return "stopped"
            code = child.poll()
            if code is not None:
                return f"the new code exited with code {code} before becoming healthy"
            if self._health(version):
                return None
            if self._clock() - started >= self._health_timeout:
                return f"no healthy answer within {self._health_timeout:g} s"
            self._sleep(self._poll_interval)

    def _record(self, pending: dict, ok: bool, error: str = "", log_tail: Optional[list] = None) -> None:
        result = {
            "ok": ok,
            "from_version": pending.get("from_version", ""),
            "to_version": pending.get("version", ""),
            "requested_by": pending.get("requested_by", ""),
            "requested_at": pending.get("requested_at", ""),
            "finished_at": _now_iso(),
        }
        if not ok:
            result["error"] = error
            result["log_tail"] = log_tail or []
        write_json_atomic(self.paths.last_result, result)

    def _prune_overlays(self) -> None:
        keep = set()
        for state in (read_json(self.paths.current), read_json(self.paths.current_prev)):
            if state and state.get("version"):
                keep.add(str(state["version"]))
        root = self.paths.root / "overlay"
        if root.is_dir():
            for entry in root.iterdir():
                if entry.name not in keep:
                    shutil.rmtree(entry, ignore_errors=True)


def main() -> None:
    data_dir = Path(os.environ.get("SMOS_DATA_DIR", "/data"))
    info = load_build_info()
    port = int(os.environ.get("SMOS_PORT", "8765"))

    def spawn(overlay: Optional[Path]) -> Child:
        env = dict(os.environ)
        if overlay is not None:
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(overlay), env.get("PYTHONPATH", "")]))
        return ProcessChild([sys.executable, "-m", "smos.api.main"], env)

    supervisor = Supervisor(data_dir, spawn=spawn, health=http_health(port), build_id=info.get("build_id"))
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: supervisor.request_stop())
    sys.exit(supervisor.run())


if __name__ == "__main__":
    main()

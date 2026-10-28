"""Prepare an update and hand over to the supervisor.

`Updater.begin` downloads and checks a release, writes `pending.json`, puts the app into
maintenance mode (writes get 503) and exits with code 75 once in-flight requests have drained.
The supervisor (smos/launcher.py) then snapshots the data, switches the code and health-checks it.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from smos.launcher import EXIT_RESTART, UpdatePaths, write_json_atomic
from smos.update import audit
from smos.update.config import UpdateConfig
from smos.update.github import GitHubError
from smos.update.status import UpdateChecker, UpdateStatus, evaluate
from smos.update.unpack import UnpackError, extract_smos, smoke_check
from smos.update.versions import format_version

log = logging.getLogger("smos.update")
COOLDOWN_S = 300.0
_SNAPSHOT_MARGIN = 1.2


class ApplyRefused(Exception):
    """The update was not started; the message is safe to show an admin."""


def _data_bytes(data_dir: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(data_dir):
        if Path(root) == Path(data_dir):
            dirs[:] = [d for d in dirs if d != "updates"]
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


class Updater:
    def __init__(
        self,
        app,
        config: UpdateConfig,
        client,
        checker: UpdateChecker,
        paths: UpdatePaths,
        *,
        running_version: str,
        exit_fn: Callable[[int], None] = os._exit,
        close_fn: Optional[Callable[[], None]] = None,
        exit_delay: float = 1.0,
        drain_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        smoke: Callable[[Path], None] = smoke_check,
    ) -> None:
        self._app, self._config, self._client, self._checker = app, config, client, checker
        self._paths, self._running = paths, running_version
        self._exit_fn, self._close_fn = exit_fn, close_fn
        self._exit_delay, self._drain_timeout = exit_delay, drain_timeout
        self._clock, self._smoke = clock, smoke
        self._busy = threading.Lock()
        self._state = "idle"
        self._cooldown_until = 0.0

    def state(self) -> str:
        """idle | staging | restarting"""
        return self._state

    # ------------------------------------------------------------------ entry point
    def begin(self, admin: str, expected_version: str) -> None:
        if not self._config.enabled:
            raise ApplyRefused("Updates are switched off on this installation.")
        if not self._busy.acquire(blocking=False):
            raise ApplyRefused("An update is already running or the server is restarting for one.")
        handed_over = False
        status: Optional[UpdateStatus] = None
        try:
            self._state = "staging"
            status = self._check_ready(expected_version)
            self._stage(status)
            self._hand_over(admin, status)
            handed_over = True
        except ApplyRefused as err:
            self._audit(admin, expected_version, status.sha if status and status.sha else "", "refused", str(err))
            raise
        except (GitHubError, UnpackError, OSError) as err:
            self._cleanup(status)
            self._cooldown_until = self._clock() + COOLDOWN_S
            self._audit(admin, expected_version, status.sha if status and status.sha else "", "failed", str(err))
            raise ApplyRefused(f"The update could not be prepared: {err}") from None
        finally:
            if not handed_over:
                self._state = "idle"
                self._busy.release()

    # ------------------------------------------------------------------ steps
    def _check_ready(self, expected_version: str) -> UpdateStatus:
        wait = self._cooldown_until - self._clock()
        if wait > 0:
            raise ApplyRefused(f"The last update attempt failed; wait {int(wait) + 1} s before trying again.")
        status = evaluate(self._client, self._running, self._checker.image_manifest, time.time())
        self._checker.record(status)
        if status.state == "check_failed":
            raise ApplyRefused(f"Could not check GitHub: {status.error}")
        if status.state == "needs_rebuild":
            raise ApplyRefused("This release needs a rebuilt image (changed: "
                               + ", ".join(status.rebuild_files) + "); rebuild instead of updating in place.")
        if status.state != "available" or status.release is None or status.sha is None:
            raise ApplyRefused("There is no newer release to install.")
        latest = format_version(status.release.version)
        if latest != expected_version:
            raise ApplyRefused(f"A different release is now the latest ({latest}, you confirmed "
                               f"{expected_version}); review it and confirm again.")
        data_dir = self._paths.data_dir
        need = int(_data_bytes(data_dir) * _SNAPSHOT_MARGIN) + self._config.max_archive_bytes
        free = shutil.disk_usage(data_dir).free
        if free < need:
            raise ApplyRefused("Not enough free disk space for the safety snapshot: need about "
                               f"{need // (1024 * 1024)} MB, have {free // (1024 * 1024)} MB.")
        return status

    def _stage(self, status: UpdateStatus) -> None:
        assert status.release is not None and status.sha is not None
        version = format_version(status.release.version)
        download = self._paths.root / f"download-{version}.tar.gz"
        staging = self._paths.staging_dir(version)
        self._paths.root.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        self._client.download_archive(status.sha, download)
        found = extract_smos(download, status.sha, staging, self._config)
        download.unlink(missing_ok=True)
        if found != version:
            raise UnpackError(f"the release's code says it is version {found}, but its tag says {version}")
        self._smoke(staging)

    def _hand_over(self, admin: str, status: UpdateStatus) -> None:
        assert status.release is not None and status.sha is not None
        write_json_atomic(self._paths.pending, {
            "version": format_version(status.release.version),
            "sha": status.sha,
            "requested_by": admin,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "from_version": self._running,
        })
        self._app.state.maintenance = True
        self._state = "restarting"
        threading.Thread(target=self._exit_when_drained, daemon=True, name="update-exit").start()

    def _exit_when_drained(self) -> None:
        time.sleep(self._exit_delay)
        deadline = time.monotonic() + self._drain_timeout
        while self._app.state.inflight > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        close = self._close_fn or getattr(self._app.state, "close", None)
        try:
            if close is not None:
                close()
        except Exception:
            log.exception("closing before the update restart failed")
        self._exit_fn(EXIT_RESTART)

    # ------------------------------------------------------------------ helpers
    def _cleanup(self, status: Optional[UpdateStatus]) -> None:
        if status is not None and status.release is not None:
            version = format_version(status.release.version)
            shutil.rmtree(self._paths.staging_dir(version), ignore_errors=True)
            (self._paths.root / f"download-{version}.tar.gz").unlink(missing_ok=True)

    def _audit(self, admin: str, to_version: str, sha: str, outcome: str, detail: str) -> None:
        try:
            with self._app.state.admin_lock:
                audit.record(self._app.state.admin_db, admin_user=admin, from_version=self._running,
                             to_version=to_version, sha=sha, outcome=outcome, detail=detail)
        except Exception:
            log.exception("could not write the update audit row")

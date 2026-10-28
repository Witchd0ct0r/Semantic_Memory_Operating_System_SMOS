"""What the dashboard knows about updates: the latest release versus the running version."""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from smos.update.config import UpdateConfig
from smos.update.github import GitHubError, Release
from smos.update.manifest import differing
from smos.update.versions import is_newer, parse_version

log = logging.getLogger("smos.update")
_FORCED_CHECK_GAP_S = 60.0


@dataclass(frozen=True)
class UpdateStatus:
    state: str  # unchecked | up_to_date | available | needs_rebuild | check_failed
    running: str
    checked_at: Optional[float]
    release: Optional[Release] = None
    sha: Optional[str] = None
    rebuild_files: tuple[str, ...] = ()
    error: Optional[str] = None


def evaluate(client, running_version: str, image_manifest: Mapping[str, str], now: float) -> UpdateStatus:
    """Ask GitHub and classify. Never raises: a failure is a `check_failed` status."""
    try:
        release = client.latest_release()
        if release is None or not is_newer(release.version, parse_version(running_version)):
            return UpdateStatus("up_to_date", running_version, now)
        sha = client.resolve_tag(release.tag)
        changed = differing(image_manifest, client.target_manifest(sha))
        state = "needs_rebuild" if changed else "available"
        return UpdateStatus(state, running_version, now, release=release, sha=sha, rebuild_files=tuple(changed))
    except GitHubError as err:
        return UpdateStatus("check_failed", running_version, now, error=str(err))
    except Exception:  # a bug here must never take the dashboard down
        log.exception("update check failed unexpectedly")
        return UpdateStatus("check_failed", running_version, now, error="unexpected error while checking")


class UpdateChecker:
    """Caches the latest status; re-checks on a timer and on request (at most once a minute)."""

    def __init__(self, config: UpdateConfig, client, running_version: str,
                 image_manifest: Mapping[str, str], clock: Callable[[], float] = time.time) -> None:
        self._config, self._client = config, client
        self._running, self._manifest, self._clock = running_version, dict(image_manifest), clock
        self._status = UpdateStatus("unchecked", running_version, None)
        self._last_attempt: Optional[float] = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running_version(self) -> str:
        return self._running

    @property
    def image_manifest(self) -> dict[str, str]:
        return dict(self._manifest)

    @property
    def client(self):
        return self._client

    def status(self) -> UpdateStatus:
        with self._lock:
            return self._status

    def retry_after(self) -> int:
        with self._lock:
            if self._last_attempt is None:
                return 0
            return max(0, math.ceil(_FORCED_CHECK_GAP_S - (self._clock() - self._last_attempt)))

    def check(self, force: bool = False) -> UpdateStatus:
        now = self._clock()
        with self._lock:
            last = self._last_attempt
            if force and last is not None and now - last < _FORCED_CHECK_GAP_S:
                return self._status
            if not force and last is not None and now - last < self._config.check_minutes * 60:
                return self._status
        if not self._busy.acquire(blocking=False):
            return self.status()  # another thread is already asking GitHub
        try:
            with self._lock:
                self._last_attempt = now
            result = evaluate(self._client, self._running, self._manifest, now)
            with self._lock:
                self._status = result
            return result
        finally:
            self._busy.release()

    def record(self, status: UpdateStatus) -> None:
        """Store a status obtained elsewhere (the apply step re-evaluates before it acts)."""
        with self._lock:
            self._status = status

    def start(self, first_delay: float = 30.0) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, args=(first_delay,), daemon=True, name="update-check")
        self._thread.start()

    def _loop(self, first_delay: float) -> None:
        if self._stop.wait(first_delay):
            return
        while True:
            self.check()
            if self._stop.wait(self._config.check_minutes * 60):
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

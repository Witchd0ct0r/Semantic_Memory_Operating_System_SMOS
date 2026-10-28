from __future__ import annotations

import time

import pytest

from smos.update.config import UpdateConfig
from smos.update.github import GitHubError, Release
from smos.update.status import UpdateChecker, evaluate

SHA = "c" * 40
IMAGE = {"pyproject.toml": "p1", "Dockerfile": "d1", "compose.yaml": "c1", "models": "m1", "smos/launcher.py": "l1"}


class FakeClient:
    def __init__(self, release=None, manifest=None, error=None) -> None:
        self.release, self.manifest, self.error = release, dict(IMAGE if manifest is None else manifest), error
        self.calls = 0

    def latest_release(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.release

    def resolve_tag(self, tag: str) -> str:
        return SHA

    def target_manifest(self, sha: str) -> dict:
        assert sha == SHA
        return self.manifest


def _release(version=(0, 3, 0)) -> Release:
    tag = "v%d.%d.%d" % version
    return Release(version=version, tag=tag, notes="n", url="https://example.org/r")


def test_no_release_is_up_to_date() -> None:
    status = evaluate(FakeClient(release=None), "0.2.0", IMAGE, now=5.0)
    assert status.state == "up_to_date" and status.running == "0.2.0" and status.checked_at == 5.0


@pytest.mark.parametrize("version", [(0, 2, 0), (0, 1, 9)])
def test_same_or_older_release_is_up_to_date(version) -> None:
    assert evaluate(FakeClient(_release(version)), "0.2.0", IMAGE, 1.0).state == "up_to_date"


def test_newer_release_available_with_sha() -> None:
    status = evaluate(FakeClient(_release()), "0.2.0", IMAGE, 1.0)
    assert status.state == "available" and status.sha == SHA and status.release.tag == "v0.3.0"
    assert status.rebuild_files == () and status.error is None


def test_changed_dockerfile_needs_rebuild_and_lists_it() -> None:
    status = evaluate(FakeClient(_release(), manifest={**IMAGE, "Dockerfile": "d2"}), "0.2.0", IMAGE, 1.0)
    assert status.state == "needs_rebuild" and status.rebuild_files == ("Dockerfile",)
    assert status.release is not None and status.sha == SHA


def test_changed_launcher_needs_rebuild() -> None:
    status = evaluate(FakeClient(_release(), manifest={**IMAGE, "smos/launcher.py": "l2"}), "0.2.0", IMAGE, 1.0)
    assert status.state == "needs_rebuild" and status.rebuild_files == ("smos/launcher.py",)


def test_missing_entries_in_the_release_need_a_rebuild_too() -> None:
    manifest = {k: v for k, v in IMAGE.items() if k != "models"}
    status = evaluate(FakeClient(_release(), manifest=manifest), "0.2.0", IMAGE, 1.0)
    assert status.state == "needs_rebuild" and status.rebuild_files == ("models",)


def test_github_error_becomes_check_failed_with_message() -> None:
    status = evaluate(FakeClient(error=GitHubError("cannot reach GitHub: boom")), "0.2.0", IMAGE, 1.0)
    assert status.state == "check_failed" and status.error == "cannot reach GitHub: boom"


def test_unexpected_exception_is_contained_as_check_failed() -> None:
    status = evaluate(FakeClient(error=RuntimeError("bug")), "0.2.0", IMAGE, 1.0)
    assert status.state == "check_failed" and "unexpected" in status.error


def test_unparseable_running_version_counts_as_oldest() -> None:
    assert evaluate(FakeClient(_release((0, 0, 1))), "0.0.0-dev", IMAGE, 1.0).state == "available"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _checker(client, clock, minutes=360) -> UpdateChecker:
    return UpdateChecker(UpdateConfig(repo="o/r", check_minutes=minutes), client, "0.2.0", IMAGE, clock=clock)


def test_checker_starts_unchecked() -> None:
    status = _checker(FakeClient(), Clock()).status()
    assert status.state == "unchecked" and status.running == "0.2.0"


def test_non_forced_check_uses_the_cache_until_the_interval_passes() -> None:
    clock, client = Clock(), FakeClient(_release())
    checker = _checker(client, clock, minutes=10)
    assert checker.check().state == "available" and client.calls == 1
    clock.now += 599
    checker.check()
    assert client.calls == 1
    clock.now += 2
    checker.check()
    assert client.calls == 2


def test_check_now_is_rate_limited_to_once_a_minute() -> None:
    clock, client = Clock(), FakeClient(_release())
    checker = _checker(client, clock)
    checker.check(force=True)
    clock.now += 30
    assert checker.retry_after() == 30
    checker.check(force=True)
    assert client.calls == 1
    clock.now += 31
    assert checker.retry_after() == 0
    checker.check(force=True)
    assert client.calls == 2


def test_failed_check_is_retried_by_the_next_forced_check_after_a_minute() -> None:
    clock, client = Clock(), FakeClient(error=GitHubError("down"))
    checker = _checker(client, clock)
    assert checker.check(force=True).state == "check_failed"
    client.error, client.release = None, _release()
    clock.now += 61
    assert checker.check(force=True).state == "available"


def test_background_loop_checks_and_stops_cleanly() -> None:
    client = FakeClient(_release())
    checker = _checker(client, time.time)
    checker.start(first_delay=0.0)
    deadline = time.time() + 5
    while client.calls == 0 and time.time() < deadline:
        time.sleep(0.02)
    assert client.calls == 1
    started = time.time()
    checker.stop()
    assert time.time() - started < 2
    assert checker.status().state == "available"

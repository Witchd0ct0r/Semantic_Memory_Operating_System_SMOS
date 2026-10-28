from __future__ import annotations

import re

import pytest

import smos
from smos import launcher
from smos.launcher import UpdatePaths
from smos.update.apply import ApplyRefused
from smos.update.config import UpdateConfig
from smos.update.github import Release
from smos.update.service import UpdateService
from smos.update.status import UpdateStatus

PASSWORD = "correct-horse-1"
SHA = "e" * 40


class FakeChecker:
    def __init__(self, status: UpdateStatus) -> None:
        self._status, self.checks, self.wait = status, [], 0

    def status(self) -> UpdateStatus:
        return self._status

    def check(self, force: bool = False) -> UpdateStatus:
        self.checks.append(force)
        return self._status

    def retry_after(self) -> int:
        return self.wait


class FakeUpdater:
    def __init__(self) -> None:
        self.calls, self.refuse, self._state = [], None, "idle"

    def begin(self, admin: str, version: str, sha: str) -> None:
        if self.refuse:
            raise ApplyRefused(self.refuse)
        self.calls.append((admin, version, sha))

    def state(self) -> str:
        return self._state


def _release(notes: str = "Fixes things.") -> Release:
    return Release(version=(0, 3, 0), tag="v0.3.0", notes=notes, url="https://github.com/o/r/releases/tag/v0.3.0")


def _status(state: str, **kw) -> UpdateStatus:
    return UpdateStatus(state, "0.2.0", 1.0, **kw)


@pytest.fixture
def admin(api):
    client, _h, app = api
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303

    def install(status: UpdateStatus, updater: FakeUpdater | None = None):
        checker, updater = FakeChecker(status), updater or FakeUpdater()
        app.state.update = UpdateService(UpdateConfig(repo="o/r"), checker, updater, None)
        return checker, updater

    def csrf() -> str:
        return re.search(r'name="csrf" value="([^"]+)"', client.get("/admin/tokens").text).group(1)

    return client, app, install, csrf


@pytest.mark.parametrize("status,expected", [
    (_status("up_to_date"), "up to date"),
    (_status("unchecked"), "not checked yet"),
    (_status("available", release=_release(), sha=SHA), "Release 0.3.0 is available"),
    (_status("needs_rebuild", release=_release(), sha=SHA, rebuild_files=("Dockerfile", "models")),
     "needs a rebuilt image"),
    (_status("check_failed", error="cannot reach GitHub: boom"), "cannot reach GitHub: boom"),
])
def test_dashboard_shows_each_banner_state(admin, status, expected) -> None:
    client, _app, install, _csrf = admin
    install(status)
    page = client.get("/admin/dashboard")
    assert page.status_code == 200 and expected in page.text
    assert f"SMOS {smos.__version__}" in page.text


def test_dashboard_banner_lists_rebuild_files(admin) -> None:
    client, _app, install, _csrf = admin
    install(_status("needs_rebuild", release=_release(), sha=SHA, rebuild_files=("Dockerfile", "models")))
    text = client.get("/admin/dashboard").text
    assert "Dockerfile" in text and "models" in text


def test_dashboard_says_why_updates_are_disabled(admin) -> None:
    client, app, _install, _csrf = admin
    app.state.update = UpdateService(UpdateConfig(repo=""), None, None, "Updates are switched off (test).")
    page = client.get("/admin/dashboard")
    assert page.status_code == 200 and "Updates are switched off (test)." in page.text
    assert "/admin/update/check" not in page.text  # nothing to check when disabled


def test_default_install_without_a_build_info_is_disabled_but_renders(admin) -> None:
    client, _app, _install, _csrf = admin  # the api fixture has no build-info.json
    page = client.get("/admin/dashboard")
    assert page.status_code == 200 and "container image" in page.text


def test_dashboard_shows_last_update_result(admin) -> None:
    client, app, install, _csrf = admin
    install(_status("up_to_date"))
    launcher.write_json_atomic(UpdatePaths(app.state.data_dir).last_result, {
        "ok": False, "to_version": "0.3.0", "from_version": "0.2.0", "finished_at": "2026-10-28T10:01:00+00:00",
        "error": "no healthy answer within 120 s", "log_tail": []})
    text = client.get("/admin/dashboard").text
    assert "rolled back" in text and "no healthy answer within 120 s" in text
    launcher.write_json_atomic(UpdatePaths(app.state.data_dir).last_result, {
        "ok": True, "to_version": "0.3.0", "from_version": "0.2.0", "finished_at": "2026-10-28T10:01:00+00:00"})
    assert "applied" in client.get("/admin/dashboard").text


def test_release_notes_are_escaped_and_not_executable(admin) -> None:
    client, _app, install, _csrf = admin
    install(_status("available", release=_release("<script>alert(1)</script><img src=x onerror=y>"), sha=SHA))
    page = client.get("/admin/update").text
    assert "<script>alert(1)</script>" not in page and "<img src=x" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_very_long_notes_do_not_break_the_page(admin) -> None:
    client, _app, install, _csrf = admin
    install(_status("available", release=_release("line\n" * 4000), sha=SHA))
    assert client.get("/admin/update").status_code == 200


def test_confirmation_page_shows_version_tag_and_sha(admin) -> None:
    client, _app, install, _csrf = admin
    install(_status("available", release=_release(), sha=SHA))
    page = client.get("/admin/update").text
    assert "0.2.0" in page and "0.3.0" in page and "v0.3.0" in page and SHA in page
    assert 'name="version" value="0.3.0"' in page and f'name="sha" value="{SHA}"' in page and "/admin/update/apply" in page


def test_no_apply_form_when_nothing_is_available(admin) -> None:
    client, _app, install, _csrf = admin
    install(_status("needs_rebuild", release=_release(), sha=SHA, rebuild_files=("Dockerfile",)))
    page = client.get("/admin/update").text
    assert "/admin/update/apply" not in page and "Dockerfile" in page


@pytest.mark.parametrize("method,path", [
    ("get", "/admin/update"), ("get", "/admin/update/status"),
    ("post", "/admin/update/check"), ("post", "/admin/update/apply")])
def test_update_routes_require_admin_login(api, method, path) -> None:
    client, headers, _app = api
    for hdrs in ({}, headers):  # an API token is not an admin session
        r = getattr(client, method)(path, headers=hdrs, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login", (path, hdrs)


def test_post_routes_reject_missing_or_wrong_csrf(admin) -> None:
    client, _app, install, _csrf = admin
    checker, updater = install(_status("available", release=_release(), sha=SHA))
    for path in ("/admin/update/check", "/admin/update/apply"):
        assert client.post(path, data={"version": "0.3.0"}).status_code == 403
        assert client.post(path, data={"version": "0.3.0", "csrf": "wrong"}).status_code == 403
    assert checker.checks == [] and updater.calls == []


def test_check_now_calls_forced_check(admin) -> None:
    client, _app, install, csrf = admin
    checker, _updater = install(_status("up_to_date"))
    r = client.post("/admin/update/check", data={"csrf": csrf()}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/dashboard"
    assert checker.checks == [True]


def test_apply_calls_begin_with_admin_and_version_then_redirects(admin) -> None:
    client, _app, install, csrf = admin
    _checker, updater = install(_status("available", release=_release(), sha=SHA))
    r = client.post("/admin/update/apply", data={"csrf": csrf(), "version": "0.3.0", "sha": SHA}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/update?applying=1"
    assert updater.calls == [("admin", "0.3.0", SHA)]


def test_apply_refusal_renders_message_with_409(admin) -> None:
    client, _app, install, csrf = admin
    updater = FakeUpdater()
    updater.refuse = "A different release is now the latest (0.4.0); review it and confirm again."
    install(_status("available", release=_release(), sha=SHA), updater)
    r = client.post("/admin/update/apply", data={"csrf": csrf(), "version": "0.3.0", "sha": SHA})
    assert r.status_code == 409 and "A different release is now the latest" in r.text


def test_apply_without_a_version_field_is_refused(admin) -> None:
    client, _app, install, csrf = admin
    _checker, updater = install(_status("available", release=_release(), sha=SHA))
    assert client.post("/admin/update/apply", data={"csrf": csrf()}).status_code == 409
    assert updater.calls == []


def test_apply_when_updates_are_disabled_is_refused(admin) -> None:
    client, app, _install, csrf = admin
    app.state.update = UpdateService(UpdateConfig(repo=""), None, None, "Updates are switched off (test).")
    r = client.post("/admin/update/apply", data={"csrf": csrf(), "version": "0.3.0", "sha": SHA})
    assert r.status_code == 409 and "switched off" in r.text


def test_status_endpoint_json_shape(admin) -> None:
    client, app, install, _csrf = admin
    install(_status("available", release=_release(), sha=SHA))
    body = client.get("/admin/update/status").json()
    assert set(body) == {"state", "updater_state", "version", "last_result"}
    assert body["state"] == "available" and body["updater_state"] == "idle" and body["version"] == "0.2.0"
    assert body["last_result"] is None
    app.state.maintenance = True  # reads keep working while the server prepares to restart
    assert client.get("/admin/update/status").status_code == 200


def test_applying_page_polls_the_status_endpoint(admin) -> None:
    client, _app, install, _csrf = admin
    install(_status("available", release=_release(), sha=SHA))
    page = client.get("/admin/update?applying=1").text
    assert "/admin/update/status" in page and 'id="progress"' in page


def test_apply_without_the_confirmed_commit_is_refused(admin) -> None:
    client, _app, install, csrf = admin
    _checker, updater = install(_status("available", release=_release(), sha=SHA))
    assert client.post("/admin/update/apply", data={"csrf": csrf(), "version": "0.3.0"}).status_code == 409
    assert updater.calls == []

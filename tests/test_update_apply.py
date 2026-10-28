from __future__ import annotations

import shutil
import threading
import time
from collections import namedtuple
from types import SimpleNamespace

import pytest

from smos import launcher
from smos.admin import tokens
from smos.launcher import UpdatePaths
from smos.update import apply as apply_module
from smos.update import audit
from smos.update.apply import ApplyRefused, Updater
from smos.update.config import UpdateConfig
from smos.update.github import GitHubClient
from smos.update.status import UpdateChecker
from smos.update.unpack import UnpackError
from tests.fake_github import FakeGitHub, json_response, release_tarball

REPO = "o/r"
SHA = "d" * 40
IMAGE = {"pyproject.toml": "p1", "Dockerfile": "d1", "compose.yaml": "c1", "models": "m1", "smos/launcher.py": "l1"}
Usage = namedtuple("Usage", "total used free")


def _routes(fake: FakeGitHub, version: str = "0.3.0", tarball: bytes | None = None, manifest=None) -> None:
    tag = f"v{version}"
    fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(
        {"tag_name": tag, "body": "notes", "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
         "draft": False, "prerelease": False})
    fake.routes[f"/repos/{REPO}/commits/{tag}"] = json_response({"sha": SHA})
    m = dict(IMAGE if manifest is None else manifest)
    root = [{"path": k, "type": "tree" if k == "models" else "blob", "sha": v}
            for k, v in m.items() if "/" not in k]
    root.append({"path": "smos", "type": "tree", "sha": "5" * 40})
    fake.routes[f"/repos/{REPO}/git/trees/{SHA}"] = json_response({"tree": root})
    fake.routes[f"/repos/{REPO}/git/trees/{'5' * 40}"] = json_response(
        {"tree": [{"path": "launcher.py", "type": "blob", "sha": m.get("smos/launcher.py", "l1")}]})
    fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = (200, {}, tarball or release_tarball(SHA, version))


@pytest.fixture
def env(api):
    _client, _headers, app = api
    with FakeGitHub() as fake:
        _routes(fake)
        cfg = UpdateConfig(repo=REPO, api_base=fake.url, archive_base=fake.url, ca_bundle=None)
        github = GitHubClient(cfg, proxies={})
        checker = UpdateChecker(cfg, github, "0.2.0", IMAGE)
        clock = SimpleNamespace(now=1000.0)
        exits, closed, done = [], [], threading.Event()

        def exit_fn(code):
            exits.append(code)
            done.set()

        def make(**overrides):
            kwargs = dict(running_version="0.2.0", exit_fn=exit_fn, close_fn=lambda: closed.append(1),
                          exit_delay=0.0, drain_timeout=2.0, smoke=lambda staged: None,
                          clock=lambda: clock.now)
            kwargs.update(overrides)
            return Updater(app, kwargs.pop("config", cfg), github, checker, UpdatePaths(app.state.data_dir), **kwargs)

        yield SimpleNamespace(app=app, fake=fake, cfg=cfg, github=github, checker=checker, clock=clock,
                              exits=exits, closed=closed, done=done, make=make,
                              paths=UpdatePaths(app.state.data_dir))


def _rows(app) -> list[dict]:
    return audit.recent(app.state.admin_db)


def test_begin_stages_writes_pending_sets_maintenance_and_exits_75(env) -> None:
    updater = env.make()
    updater.begin("alice", "0.3.0")
    assert env.done.wait(5) and env.exits == [75] and env.closed == [1]
    pending = launcher.read_json(env.paths.pending)
    assert pending["version"] == "0.3.0" and pending["sha"] == SHA
    assert pending["requested_by"] == "alice" and pending["from_version"] == "0.2.0" and pending["requested_at"]
    assert (env.paths.staging_dir("0.3.0") / "smos" / "__init__.py").is_file()
    assert not list(env.paths.root.glob("download-*"))
    assert env.app.state.maintenance is True and updater.state() == "restarting"


def test_real_smoke_check_is_wired_in(env) -> None:
    updater = env.make(smoke=apply_module.smoke_check)  # staged package imports smos.api.app from the staging dir
    updater.begin("alice", "0.3.0")
    assert env.done.wait(10) and env.exits == [75]


def test_refuses_when_disabled(env) -> None:
    updater = env.make(config=UpdateConfig(repo="", ca_bundle=None))
    with pytest.raises(ApplyRefused, match="switched off|disabled"):
        updater.begin("alice", "0.3.0")
    assert not env.paths.pending.exists() and env.app.state.maintenance is False


def test_refuses_needs_rebuild_naming_files_and_records_it(env) -> None:
    _routes(env.fake, manifest={**IMAGE, "Dockerfile": "d2"})
    with pytest.raises(ApplyRefused, match="Dockerfile"):
        env.make().begin("alice", "0.3.0")
    assert not env.paths.pending.exists() and env.app.state.maintenance is False
    assert not any("archive" in r for r in env.fake.requests)  # nothing was downloaded
    row = _rows(env.app)[0]
    assert row["outcome"] == "refused" and row["admin_user"] == "alice" and "Dockerfile" in row["detail"]


def test_refuses_when_nothing_newer_is_available(env) -> None:
    _routes(env.fake, version="0.2.0")
    with pytest.raises(ApplyRefused, match="no newer release"):
        env.make().begin("alice", "0.2.0")


def test_refuses_when_latest_changed_since_confirmation(env) -> None:
    with pytest.raises(ApplyRefused, match="different release"):
        env.make().begin("alice", "0.2.9")
    assert not env.paths.pending.exists()


def test_refuses_when_github_is_unreachable(env) -> None:
    updater = env.make()
    env.fake.routes.clear()  # every call 404s: no release can be found
    with pytest.raises(ApplyRefused, match="no newer release"):
        updater.begin("alice", "0.3.0")


def test_refuses_on_low_disk(env, monkeypatch) -> None:
    (env.app.state.data_dir / "filler.bin").write_bytes(b"x" * 10_000)
    monkeypatch.setattr(apply_module.shutil, "disk_usage", lambda path: Usage(10**9, 10**9 - 1000, 1000))
    with pytest.raises(ApplyRefused, match="free disk space"):
        env.make().begin("alice", "0.3.0")
    assert not any("archive" in r for r in env.fake.requests)


def test_refuses_when_staged_version_differs_from_tag(env) -> None:
    env.fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = (200, {}, release_tarball(SHA, "0.3.1"))
    with pytest.raises(ApplyRefused, match="0.3.1"):
        env.make().begin("alice", "0.3.0")
    assert not env.paths.staging_dir("0.3.0").exists() and not list(env.paths.root.glob("download-*"))
    assert env.app.state.maintenance is False and not env.paths.pending.exists()


def test_smoke_failure_cleans_up_clears_maintenance_and_records(env) -> None:
    def broken(staged):
        raise UnpackError("the new code does not import: boom")

    with pytest.raises(ApplyRefused, match="does not import"):
        env.make(smoke=broken).begin("alice", "0.3.0")
    assert not env.paths.staging_dir("0.3.0").exists() and env.app.state.maintenance is False
    assert not env.paths.pending.exists() and env.exits == []
    assert _rows(env.app)[0]["outcome"] == "failed"


def test_second_concurrent_begin_is_refused(env) -> None:
    gate, entered = threading.Event(), threading.Event()

    def slow_archive(path):
        entered.set()
        gate.wait(10)
        return 200, {}, release_tarball(SHA, "0.3.0")

    env.fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = slow_archive
    updater = env.make()
    first_error = []

    def first():
        try:
            updater.begin("alice", "0.3.0")
        except Exception as err:  # noqa: BLE001
            first_error.append(err)

    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(10)
    assert updater.state() == "staging"
    with pytest.raises(ApplyRefused, match="already running"):
        updater.begin("bob", "0.3.0")
    gate.set()
    thread.join(10)
    assert first_error == [] and env.done.wait(5) and env.exits == [75]


def test_second_begin_after_success_is_also_refused(env) -> None:
    updater = env.make()
    updater.begin("alice", "0.3.0")
    with pytest.raises(ApplyRefused, match="already running|restarting"):
        updater.begin("bob", "0.3.0")


def test_cooldown_after_failed_staging(env) -> None:
    def broken(staged):
        raise UnpackError("boom")

    updater = env.make(smoke=broken)
    with pytest.raises(ApplyRefused):
        updater.begin("alice", "0.3.0")
    with pytest.raises(ApplyRefused, match="wait"):
        updater.begin("alice", "0.3.0")
    env.clock.now += 301
    with pytest.raises(ApplyRefused, match="boom"):  # allowed to try again, and fails the same way
        updater.begin("alice", "0.3.0")


def test_exit_waits_for_inflight_requests(env) -> None:
    env.app.state.inflight = 1
    env.make(drain_timeout=10.0).begin("alice", "0.3.0")
    assert not env.done.wait(0.4)
    env.app.state.inflight = 0
    assert env.done.wait(3) and env.exits == [75]


def test_exit_gives_up_waiting_after_the_drain_timeout(env) -> None:
    env.app.state.inflight = 5  # never drains
    env.make(drain_timeout=0.3).begin("alice", "0.3.0")
    assert env.done.wait(5) and env.exits == [75]


# ------------------------------------------------------------------ maintenance mode

def _rw(app):
    return {"Authorization": "Bearer " + tokens.create_token(app.state.admin_db, "writer", scope="rw")[1]}


def test_writes_get_503_while_reads_and_dashboard_work(api) -> None:
    client, headers, app = api
    client.post("/admin/login", data={"username": "admin", "password": "correct-horse-1"}, follow_redirects=False)
    csrf = client.get("/admin/tokens").text.split('name="csrf" value="')[1].split('"')[0]
    app.state.maintenance = True
    r = client.post("/api/v1/memories", json={"type": "doc", "content": "Not stored during the update window."},
                    headers=headers)
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert client.post("/api/v1/query", json={"query": "radiated emissions knowledge"}, headers=headers).status_code == 200
    assert client.get("/admin/dashboard").status_code == 200
    blocked = client.post("/admin/invitations", data={"csrf": csrf, "label": "x", "scope": "read"})
    assert blocked.status_code == 503
    assert client.post("/admin/login", data={"username": "admin", "password": "x"}).status_code == 503
    assert client.get("/api/v1/health").status_code == 200
    app.state.maintenance = False
    assert client.post("/api/v1/memories", json={"type": "doc", "content": "Stored after the window."},
                       headers=headers).status_code == 200


def test_unauthenticated_write_is_still_401_in_maintenance(api) -> None:
    client, _headers, app = api
    app.state.maintenance = True
    assert client.post("/api/v1/memories", json={}).status_code == 401


def test_inflight_counter_returns_to_zero(api) -> None:
    client, headers, app = api
    for _ in range(3):
        client.post("/api/v1/query", json={"query": "anything"}, headers=headers)
        client.get("/admin/login")
        client.get("/definitely-not-a-route")
    assert app.state.inflight == 0

from __future__ import annotations

import io
import re
from pathlib import Path

from fastapi.testclient import TestClient

from smos.admin.backup import validate_backup
from smos.memory.schemas import MemoryObject

PASSWORD = "correct-horse-1"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match
    return match.group(1)


def _login(client: TestClient) -> None:
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303


def _post(client: TestClient, path: str, files=None, **data):
    token = _csrf(client.get("/admin/settings").text)
    return client.post(path, data={"csrf": token, **data}, files=files, follow_redirects=False)


def _fill(app) -> None:
    for text in ("settings alpha sentence", "settings beta sentence"):
        app.state.store.store(MemoryObject(type="doc", content=text))
    app.state.store._save_index()


def test_settings_page_shows_three_sections_and_needs_login(api) -> None:
    client, _h, _app = api
    assert client.get("/admin/settings", follow_redirects=False).status_code == 303
    _login(client)
    html = client.get("/admin/settings").text
    for heading in ("Backup", "Load backup", "Pentest mode"):
        assert heading in html
    assert 'href="/admin/settings"' in html  # in the navigation
    assert "PENTEST MODE ACTIVE" not in html


def test_every_post_needs_login_and_csrf(api) -> None:
    client, _h, app = api
    paths = ["/admin/settings/backup", "/admin/settings/load", "/admin/settings/pentest/enable",
             "/admin/settings/pentest/disable"]
    for path in paths:
        anon = client.post(path, data={"csrf": "x"}, follow_redirects=False)
        assert anon.status_code == 303 and anon.headers["location"] == "/admin/login", path
    _login(client)
    client.get("/admin/settings")
    for path in paths:
        assert client.post(path, data={"csrf": "wrong"}, follow_redirects=False).status_code == 403, path
    assert app.state.pentest is False


def test_backup_download_is_a_valid_backup(api, tmp_path: Path) -> None:
    client, _h, app = api
    _fill(app)
    _login(client)
    r = _post(client, "/admin/settings/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"] and "smos-backup-" in r.headers["content-disposition"]
    assert "no-store" in r.headers["cache-control"]
    saved = tmp_path / "dl.zip"
    saved.write_bytes(r.content)
    work = tmp_path / "work"
    work.mkdir()
    assert validate_backup(saved, work)["counts"]["memories"] == 2
    assert not list(app.state.data_dir.glob("backup-*.zip"))  # the temporary file is gone


def test_load_good_file_then_garbage(api, tmp_path: Path) -> None:
    client, _h, app = api
    _fill(app)
    _login(client)
    good = _post(client, "/admin/settings/backup").content
    app.state.store.store(MemoryObject(type="doc", content="written after the backup"))

    ok = _post(client, "/admin/settings/load", files={"file": ("b.zip", io.BytesIO(good), "application/zip")})
    assert ok.status_code == 200 and "Backup loaded" in ok.text
    assert app.state.store.stats()["memories"] == 2

    bad = _post(client, "/admin/settings/load", files={"file": ("b.zip", io.BytesIO(b"garbage"), "application/zip")})
    assert bad.status_code == 400 and "not a SMOS backup" in bad.text
    assert app.state.store.stats()["memories"] == 2
    missing = _post(client, "/admin/settings/load")
    assert missing.status_code == 400
    assert not list(app.state.data_dir.glob("upload-*")) and not list(app.state.data_dir.glob("load-tmp-*"))


def test_oversized_upload_is_413(api, monkeypatch) -> None:
    client, _h, app = api
    _fill(app)
    _login(client)
    good = _post(client, "/admin/settings/backup").content
    monkeypatch.setenv("SMOS_MAX_BACKUP_MB", "0.0001")
    r = _post(client, "/admin/settings/load", files={"file": ("b.zip", io.BytesIO(good), "application/zip")})
    assert r.status_code == 413
    assert app.state.store.stats()["memories"] == 2
    assert not list(app.state.data_dir.glob("upload-*"))


def test_pentest_needs_the_typed_word_and_toggles(api) -> None:
    client, _h, app = api
    _fill(app)
    _login(client)
    assert _post(client, "/admin/settings/pentest/enable").status_code == 400
    assert _post(client, "/admin/settings/pentest/enable", confirm="yes").status_code == 400
    assert app.state.pentest is False

    on = _post(client, "/admin/settings/pentest/enable", confirm="PENTEST")
    assert on.status_code == 200 and app.state.pentest is True and "PENTEST MODE ACTIVE" in on.text
    assert app.state.store.stats()["memories"] == 0

    assert _post(client, "/admin/settings/pentest/disable").status_code == 400
    off = _post(client, "/admin/settings/pentest/disable", confirm="PENTEST")
    assert off.status_code == 200 and app.state.pentest is False and "PENTEST MODE ACTIVE" not in off.text
    assert app.state.store.stats()["memories"] == 2


def test_backup_and_load_are_disabled_and_refused_in_pentest_mode(api) -> None:
    client, _h, app = api
    _login(client)
    _post(client, "/admin/settings/pentest/enable", confirm="PENTEST")
    html = client.get("/admin/settings").text
    assert html.count("disabled") >= 2
    assert _post(client, "/admin/settings/backup").status_code == 409
    r = _post(client, "/admin/settings/load", files={"file": ("b.zip", io.BytesIO(b"x"), "application/zip")})
    assert r.status_code == 409
    assert app.state.pentest is True

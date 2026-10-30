from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from smos.admin import tokens

PASSWORD = "correct-horse-1"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "no csrf field in page"
    return match.group(1)


def _login(client: TestClient) -> None:
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text


def _expire_in(app, token_id: int, delta: timedelta) -> None:
    app.state.admin_db.execute("UPDATE tokens SET expires_at = ? WHERE id = ?",
                               ((datetime.now(timezone.utc) + delta).isoformat(), token_id))
    app.state.admin_db.commit()


def _post(client: TestClient, path: str, **data):
    page = client.get("/admin/tokens")
    return client.post(path, data={"csrf": _csrf(page.text), **data}, follow_redirects=False)


def test_access_page_shows_expiry_states(api) -> None:
    client, _h, app = api
    _login(client)
    db = app.state.admin_db
    near, _ = tokens.create_token(db, "near-token")
    dead, _ = tokens.create_token(db, "dead-token")
    spun, _ = tokens.create_token(db, "spun-token")
    _expire_in(app, near, timedelta(days=2))
    _expire_in(app, dead, timedelta(days=-1))
    db.execute("UPDATE tokens SET rotating = 1 WHERE id = ?", (spun,))
    db.commit()

    html = client.get("/admin/tokens").text
    assert "Expires" in html
    assert "expired: rotate needed" in html and "rotation pending" in html
    assert html.count("Extend 30 days") == 1  # only the token inside the 3-day window
    assert f"/admin/tokens/{near}/extend" in html and f"/admin/tokens/{dead}/extend" not in html
    assert html.count("/rotate") >= 3  # every non-revoked token can be rotated


def test_extend_adds_30_days_and_outside_window_is_refused(api) -> None:
    client, _h, app = api
    _login(client)
    db = app.state.admin_db
    near, _ = tokens.create_token(db, "near")
    far, _ = tokens.create_token(db, "far")
    _expire_in(app, near, timedelta(days=2))
    before = db.execute("SELECT expires_at FROM tokens WHERE id = ?", (near,)).fetchone()[0]

    ok = _post(client, f"/admin/tokens/{near}/extend")
    assert ok.status_code == 303 and ok.headers["location"] == "/admin/tokens"
    after = db.execute("SELECT expires_at FROM tokens WHERE id = ?", (near,)).fetchone()[0]
    assert datetime.fromisoformat(after) - datetime.fromisoformat(before) == timedelta(days=30)

    far_before = db.execute("SELECT expires_at FROM tokens WHERE id = ?", (far,)).fetchone()[0]
    refused = _post(client, f"/admin/tokens/{far}/extend")
    assert refused.status_code == 409
    assert db.execute("SELECT expires_at FROM tokens WHERE id = ?", (far,)).fetchone()[0] == far_before


def test_rotate_shows_link_once_old_token_dies_and_claim_replaces_it(api) -> None:
    client, headers, app = api
    _login(client)
    page = _post(client, "/admin/tokens/1/rotate")  # token 1 is the fixture token, bearer in `headers`
    assert page.status_code == 200, page.text
    match = re.search(r"/claim/([A-Za-z0-9_\-]+)", page.text)
    assert match, "no rotation link shown"
    link = match.group(0)
    assert client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers).status_code == 401

    user = TestClient(app)
    claim_page = user.get(link)
    assert claim_page.status_code == 200 and "replaces" in claim_page.text
    claimed = user.post(link)
    new_token = re.search(r"smos_[A-Za-z0-9_\-]+", claimed.text).group(0)
    ok = user.post("/api/v1/query", json={"query": "anything at all here"},
                   headers={"Authorization": f"Bearer {new_token}"})
    assert ok.status_code == 200
    assert user.post(link).status_code == 404  # single use
    html = client.get("/admin/tokens").text
    assert new_token not in html and link not in html
    user.close()


def test_rotate_unknown_or_revoked_token_is_404(api) -> None:
    client, _h, app = api
    _login(client)
    assert _post(client, "/admin/tokens/9999/rotate").status_code == 404
    tid, _ = tokens.create_token(app.state.admin_db, "gone")
    tokens.revoke_token(app.state.admin_db, tid)
    assert _post(client, f"/admin/tokens/{tid}/rotate").status_code == 404


def test_new_routes_need_login_and_csrf(api) -> None:
    client, _h, app = api
    for action in ("extend", "rotate"):
        anon = client.post(f"/admin/tokens/1/{action}", data={"csrf": "x"}, follow_redirects=False)
        assert anon.status_code == 303 and anon.headers["location"] == "/admin/login"
    _login(client)
    client.get("/admin/tokens")
    for action in ("extend", "rotate"):
        assert client.post(f"/admin/tokens/1/{action}", data={}, follow_redirects=False).status_code == 403
        assert client.post(f"/admin/tokens/1/{action}", data={"csrf": "wrong"}, follow_redirects=False).status_code == 403

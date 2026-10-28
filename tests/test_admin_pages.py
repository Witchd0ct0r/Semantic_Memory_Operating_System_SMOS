from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from smos.admin import invitations, tokens
from smos.api.app import create_app

PASSWORD = "correct-horse-1"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "no csrf field in page"
    return match.group(1)


def _login(client: TestClient) -> None:
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text


def _new_invitation_link(client: TestClient, label: str = "alice") -> str:
    page = client.get("/admin/tokens")
    r = client.post("/admin/invitations", data={"label": label, "csrf": _csrf(page.text)})
    assert r.status_code == 200, r.text
    match = re.search(r"/claim/([A-Za-z0-9_\-]+)", r.text)
    assert match, "no claim link shown"
    return match.group(0)


def test_pages_redirect_to_login_when_anonymous(api) -> None:
    client, _h, _app = api
    for path in ["/admin/tokens", "/admin/invitations", "/admin/usage"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login", path


def test_login_wrong_password_rejected(api) -> None:
    client, _h, _app = api
    r = client.post("/admin/login", data={"username": "admin", "password": "nope"}, follow_redirects=False)
    assert r.status_code == 401
    assert client.get("/admin/tokens", follow_redirects=False).status_code == 303


def test_repeated_failed_logins_are_throttled(api) -> None:
    client, _h, _app = api
    for _ in range(5):
        client.post("/admin/login", data={"username": "admin", "password": "nope"})
    r = client.post("/admin/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 429


def test_logout_ends_session(api) -> None:
    client, _h, _app = api
    _login(client)
    page = client.get("/admin/tokens")
    client.post("/admin/logout", data={"csrf": _csrf(page.text)})
    assert client.get("/admin/tokens", follow_redirects=False).status_code == 303


def test_full_invitation_flow(api) -> None:
    client, _h, app = api
    _login(client)
    link = _new_invitation_link(client, "alice")

    user = TestClient(app, headers={"user-agent": "UnitTest-UA/1.0"})
    page = user.get(link)
    assert page.status_code == 200 and "Create token" in page.text
    claimed = user.post(link)
    assert claimed.status_code == 200
    found = re.search(r"smos_[A-Za-z0-9_\-]+", claimed.text)
    assert found, "token not shown to the user"
    token = found.group(0)

    admin_view = client.get("/admin/tokens").text
    assert "alice" in admin_view and "UnitTest-UA/1.0" in admin_view and "testclient" in admin_view
    assert token not in admin_view

    r = user.post("/api/v1/query", json={"query": "anything at all here"},
                  headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    user.close()


def test_claim_link_second_use_shows_invalid_page(api) -> None:
    client, _h, app = api
    _login(client)
    link = _new_invitation_link(client)
    user = TestClient(app)
    assert user.post(link).status_code == 200
    again = user.post(link)
    assert again.status_code == 404 and "not valid" in again.text
    assert user.get(link).status_code == 404
    assert len(tokens.list_tokens(app.state.admin_db)) == 2  # fixture token + one claimed
    user.close()


def test_expired_and_revoked_link_show_same_invalid_page(api) -> None:
    client, _h, app = api
    db = app.state.admin_db
    _id, expired = invitations.create_invitation(db, "old", "admin", ttl_hours=-1)
    rev_id, revoked = invitations.create_invitation(db, "gone", "admin")
    invitations.revoke_invitation(db, rev_id)
    pages = [client.get(f"/claim/{s}") for s in (expired, revoked, "wrong-secret")]
    assert {p.status_code for p in pages} == {404}
    assert len({p.text for p in pages}) == 1


def test_claim_page_no_store_and_no_plaintext_in_admin_pages(api) -> None:
    client, _h, app = api
    _login(client)
    link = _new_invitation_link(client)
    user = TestClient(app)
    assert "no-store" in user.get(link).headers.get("cache-control", "")
    claimed = user.post(link)
    assert "no-store" in claimed.headers.get("cache-control", "")
    token = re.search(r"smos_[A-Za-z0-9_\-]+", claimed.text).group(0)
    for path in ["/admin/tokens", "/admin/invitations", "/admin/usage"]:
        assert token not in client.get(path).text, path
    user.close()


def test_invalid_claim_attempts_throttled(api) -> None:
    client, _h, _app = api
    codes = [client.get("/claim/bad-secret").status_code for _ in range(7)]
    assert codes[0] == 404 and codes[-1] == 429


def test_trust_proxy_uses_forwarded_ip_only_when_enabled(api, monkeypatch) -> None:
    client, _h, app = api
    _login(client)
    forwarded = {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}

    link = _new_invitation_link(client, "direct")
    TestClient(app).post(link, headers=forwarded)
    monkeypatch.setenv("SMOS_TRUST_PROXY", "1")
    link2 = _new_invitation_link(client, "proxied")
    TestClient(app).post(link2, headers=forwarded)

    rows = {r["name"]: r for r in tokens.list_tokens(app.state.admin_db)}
    assert rows["direct"]["claimed_ip"] == "testclient"
    assert rows["proxied"]["claimed_ip"] == "9.9.9.9"


def test_no_admin_route_creates_a_token_directly(api) -> None:
    client, _h, _app = api
    _login(client)
    page = client.get("/admin/tokens")
    r = client.post("/admin/tokens", data={"name": "x", "csrf": _csrf(page.text)})
    assert r.status_code in (404, 405)


def test_revoked_token_stops_working(api) -> None:
    client, headers, app = api
    _login(client)
    token_id = tokens.list_tokens(app.state.admin_db)[0]["id"]
    page = client.get("/admin/tokens")
    r = client.post(f"/admin/tokens/{token_id}/revoke", data={"csrf": _csrf(page.text)}, follow_redirects=False)
    assert r.status_code == 303
    assert client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers).status_code == 401


def test_usage_page_lists_calls_per_token(api) -> None:
    client, headers, app = api
    client.post("/api/v1/query", json={"query": "anything at all here"}, headers=headers)
    _login(client)
    token_id = tokens.list_tokens(app.state.admin_db)[0]["id"]
    overview = client.get("/admin/usage").text
    assert "test-token" in overview
    detail = client.get(f"/admin/usage?token={token_id}").text
    assert "/api/v1/query" in detail


def test_post_without_csrf_rejected(api) -> None:
    client, _h, _app = api
    _login(client)
    r = client.post("/admin/invitations", data={"label": "alice"})
    assert r.status_code == 403
    assert invitations.list_invitations(_app.state.admin_db) == []


def test_startup_without_admin_env_fails_clearly(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="SMOS_ADMIN_USER.*SMOS_ADMIN_PASSWORD"):
        create_app(tmp_path / "fresh")


def _invite(client, label: str, scope: str | None = None):
    page = client.get("/admin/tokens")
    data = {"label": label, "csrf": _csrf(page.text)}
    if scope is not None:
        data["scope"] = scope
    return client.post("/admin/invitations", data=data)


def test_invitation_form_offers_scope_defaulting_to_read(api) -> None:
    client, _h, _app = api
    _login(client)
    page = client.get("/admin/tokens").text
    assert 'name="scope"' in page
    assert re.search(r'value="read"[^>]*selected|selected[^>]*value="read"', page)


def test_scope_flows_from_invitation_to_token_and_pages(api) -> None:
    client, _h, app = api
    _login(client)
    for scope, words in (("read", "read-only"), ("rw", "read and write")):
        created = _invite(client, f"user-{scope}", scope)
        assert created.status_code == 200 and words in created.text
        link = re.search(r"/claim/[A-Za-z0-9_\-]+", created.text).group(0)
        user = TestClient(app)
        assert words in user.get(link).text
        claimed = user.post(link)
        assert words in claimed.text
        token = re.search(r"smos_[A-Za-z0-9_\-]+", claimed.text).group(0)
        assert tokens.authenticate_with_scope(app.state.admin_db, token)[1] == scope
        user.close()
    access = client.get("/admin/tokens").text
    assert "user-read" in access and "user-rw" in access


def test_missing_scope_defaults_to_read(api) -> None:
    client, _h, app = api
    _login(client)
    assert "read-only" in _invite(client, "no-scope-sent").text


def test_unknown_scope_is_rejected(api) -> None:
    client, _h, app = api
    _login(client)
    r = _invite(client, "evil", "superuser")
    assert r.status_code == 400
    assert invitations.list_invitations(app.state.admin_db) == []

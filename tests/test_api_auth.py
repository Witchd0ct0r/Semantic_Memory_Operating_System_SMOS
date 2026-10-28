from __future__ import annotations

from smos.admin import tokens, usage


def test_no_header_401(api) -> None:
    client, _h, _app = api
    r = client.post("/api/v1/query", json={"query": "anything at all"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].lower().startswith("bearer")


def test_malformed_header_401(api) -> None:
    client, _h, _app = api
    for value in ["Bearer", "Bearer ", "token abc", "abc", "Bearer  "]:
        r = client.post("/api/v1/query", json={"query": "anything at all"}, headers={"Authorization": value})
        assert r.status_code == 401, value


def test_wrong_scheme_401(api) -> None:
    client, headers, _app = api
    plain = headers["Authorization"].split(" ", 1)[1]
    r = client.post("/api/v1/query", json={"query": "anything at all"}, headers={"Authorization": f"Basic {plain}"})
    assert r.status_code == 401


def test_unknown_token_401(api) -> None:
    client, _h, _app = api
    r = client.post("/api/v1/query", json={"query": "anything at all"}, headers={"Authorization": "Bearer smos_nope"})
    assert r.status_code == 401


def test_revoked_token_401(api) -> None:
    client, headers, app = api
    token_id = tokens.list_tokens(app.state.admin_db)[0]["id"]
    assert client.post("/api/v1/query", json={"query": "anything at all"}, headers=headers).status_code == 200
    tokens.revoke_token(app.state.admin_db, token_id)
    assert client.post("/api/v1/query", json={"query": "anything at all"}, headers=headers).status_code == 401


def test_token_in_query_string_rejected(api) -> None:
    client, headers, _app = api
    plain = headers["Authorization"].split(" ", 1)[1]
    r = client.post(f"/api/v1/query?token={plain}&access_token={plain}", json={"query": "anything at all"})
    assert r.status_code == 401


def test_health_open_and_minimal(api) -> None:
    client, _h, _app = api
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "version"}


def test_openapi_and_docs_require_credentials(api) -> None:
    client, headers, _app = api
    for path in ["/api/v1/openapi.json", "/api/v1/docs", "/openapi.json", "/docs", "/redoc"]:
        assert client.get(path).status_code in (401, 404), path
    assert client.get("/api/v1/openapi.json").status_code == 401
    assert client.get("/api/v1/docs").status_code == 401
    assert client.get("/api/v1/openapi.json", headers=headers).status_code == 200


def test_every_route_in_schema_is_protected(api) -> None:
    client, _h, app = api
    checked = 0
    for path, item in app.openapi()["paths"].items():
        if path == "/api/v1/health":
            continue
        concrete = path.replace("{node_id}", "x").replace("{id}", "x").replace("{key}", "x")
        for method in item:
            r = client.request(method.upper(), concrete, json={})
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
            checked += 1
    assert checked >= 10


def test_401_is_logged_without_token_id(api) -> None:
    client, _h, app = api
    client.post("/api/v1/query", json={"query": "anything at all"})
    rows = usage.recent(app.state.admin_db, token_id=None, limit=5)
    assert rows[0]["status"] == 401
    assert rows[0]["token_id"] is None


def test_authenticated_call_logged_with_token_id(api) -> None:
    client, headers, app = api
    client.post("/api/v1/query", json={"query": "anything at all"}, headers=headers)
    token_id = tokens.list_tokens(app.state.admin_db)[0]["id"]
    rows = usage.recent(app.state.admin_db, token_id=token_id, limit=5)
    assert rows and rows[0]["path"] == "/api/v1/query"


def test_usage_row_has_no_body(api) -> None:
    client, headers, app = api
    secret_text = "very-secret-body-content-xyz"
    client.post("/api/v1/query", json={"query": secret_text}, headers=headers)
    dump = str(app.state.admin_db.execute("SELECT * FROM usage_log").fetchall())
    assert secret_text not in dump

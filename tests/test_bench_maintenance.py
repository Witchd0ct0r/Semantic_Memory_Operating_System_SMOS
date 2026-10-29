from __future__ import annotations

import pytest

PASSWORD = "correct-horse-1"


def _paths(app) -> list[tuple[str, str]]:
    found = []
    for path, methods in app.openapi()["paths"].items():
        for method in methods:
            if method.lower() in ("get", "post", "put", "delete", "patch"):
                found.append((method.upper(), path.replace("{", "x").replace("}", "")))
    return found


def test_every_openapi_route_returns_503_during_benchmark_except_health(api) -> None:
    client, headers, app = api
    routes = _paths(app) + [("GET", "/api/v1/openapi.json"), ("GET", "/api/v1/docs")]
    assert len(routes) > 10
    app.state.benchmark_active = True
    try:
        for method, path in routes:
            response = client.request(method, path, headers=headers, json={} if method != "GET" else None)
            if path == "/api/v1/health":
                assert response.status_code == 200, path
            else:
                assert response.status_code == 503, f"{method} {path} -> {response.status_code}"
                assert response.headers.get("retry-after") == "30"
    finally:
        app.state.benchmark_active = False
    # and everything serves again afterwards
    assert client.post("/api/v1/query", headers=headers, json={"query": "anything at all", "k": 1}).status_code == 200


def test_requests_without_a_token_also_get_503_not_401_during_benchmark(api) -> None:
    client, _headers, app = api
    app.state.benchmark_active = True
    try:
        assert client.post("/api/v1/query", json={"query": "no token here", "k": 1}).status_code == 503
    finally:
        app.state.benchmark_active = False


def test_admin_pages_still_served_during_benchmark(api) -> None:
    client, _headers, app = api
    assert client.post("/admin/login", data={"username": "admin", "password": PASSWORD},
                       follow_redirects=False).status_code == 303
    app.state.benchmark_active = True
    try:
        assert client.get("/admin/dashboard").status_code == 200
        assert client.get("/admin/tokens").status_code == 200
    finally:
        app.state.benchmark_active = False


def test_api_inflight_counter_returns_to_zero(api) -> None:
    client, headers, app = api
    assert app.state.api_inflight == 0
    client.post("/api/v1/query", headers=headers, json={"query": "counting requests", "k": 1})
    client.get("/admin/login")
    assert app.state.api_inflight == 0

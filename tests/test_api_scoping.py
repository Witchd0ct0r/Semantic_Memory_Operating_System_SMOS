from __future__ import annotations

from tests.test_api_routes import _store


def _seed(client, h) -> dict[str, str]:
    return {
        "a": _store(client, h, "Brake testing procedure for passenger cars, first norm.", tags=["norm:A"]),
        "b": _store(client, h, "Brake testing procedure for passenger cars, second norm.", tags=["norm:B"]),
        "ab": _store(client, h, "Brake testing procedure for passenger cars, shared clause.", tags=["norm:A", "norm:B"]),
    }


def _query(client, h, **body):
    return client.post("/api/v1/query", json={"query": "brake testing procedure for cars", **body}, headers=h)


def test_query_tags_restrict_over_http(api) -> None:
    client, h, _app = api
    ids = _seed(client, h)
    r = _query(client, h, k=10, tags=["norm:A"])
    assert r.status_code == 200
    body = r.json()
    assert {x["id"] for x in body["results"]} == {ids["a"], ids["ab"]}
    assert body["total"] == 2 and body["unknown_tags"] == []


def test_tags_all_and_none_over_http(api) -> None:
    client, h, _app = api
    ids = _seed(client, h)
    assert {x["id"] for x in _query(client, h, k=10, tags_all=["norm:A", "norm:B"]).json()["results"]} == {ids["ab"]}
    assert {x["id"] for x in _query(client, h, k=10, tags=["norm:A"], tags_none=["norm:B"]).json()["results"]} == {ids["a"]}


def test_offset_cap_is_400(api) -> None:
    client, h, _app = api
    _seed(client, h)
    bad = _query(client, h, k=20, offset=990)
    assert bad.status_code == 400 and "1000" in bad.json()["detail"]
    assert _query(client, h, k=20, offset=980).status_code == 200
    rec = client.post("/api/v1/recall", json={"query": "brake testing procedure", "k": 20, "offset": 990}, headers=h)
    assert rec.status_code == 400


def test_unknown_tags_reported(api) -> None:
    client, h, _app = api
    _seed(client, h)
    body = _query(client, h, k=5, tags=["norm:TYPO", "norm:A"]).json()
    assert body["unknown_tags"] == ["norm:TYPO"] and body["results"]
    empty = _query(client, h, k=5, tags_all=["norm:A", "norm:TYPO"]).json()
    assert empty["results"] == [] and empty["unknown_tags"] == ["norm:TYPO"]


def test_recall_component_uses_same_filter(api) -> None:
    client, h, _app = api
    ids = _seed(client, h)
    r = client.post("/api/v1/recall", json={"query": "brake testing procedure for cars", "strategy": "repo",
                                            "k": 10, "component": "norm:B"}, headers=h)
    assert r.status_code == 200
    assert {x["id"] for x in r.json()["results"]} == {ids["b"], ids["ab"]}
    hybrid = client.post("/api/v1/recall", json={"query": "brake testing procedure for cars", "k": 10,
                                                 "tags": ["norm:B"], "tags_none": ["norm:A"]}, headers=h)
    assert {x["id"] for x in hybrid.json()["results"]} == {ids["b"]}


def test_read_token_can_query_with_tags(api) -> None:
    from smos.admin import tokens
    client, rw, app = api
    _seed(client, rw)
    _tid, read_tok = tokens.create_token(app.state.admin_db, "reader", scope="read")
    r = _query(client, {"Authorization": f"Bearer {read_tok}"}, k=5, tags=["norm:A"])
    assert r.status_code == 200 and r.json()["total"] == 2

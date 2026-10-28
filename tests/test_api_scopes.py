from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smos.admin import tokens, usage

# The complete list of operations that change database state. Pinned on purpose: a new
# endpoint makes test_every_operation_is_classified fail until someone classifies it here.
WRITE_OPERATIONS = {
    ("POST", "/api/v1/memories"),
    ("DELETE", "/api/v1/memories/{id}"),
    ("POST", "/api/v1/memories/derived"),
    ("POST", "/api/v1/links"),
    ("PUT", "/api/v1/nodes/{node_id}/summary"),
    ("POST", "/api/v1/documents"),
    ("POST", "/api/v1/verbatim"),
}
READ_OPERATIONS = {
    ("POST", "/api/v1/query"),
    ("POST", "/api/v1/recall"),
    ("POST", "/api/v1/graph/query"),
    ("POST", "/api/v1/requirement-context"),
    ("GET", "/api/v1/nodes/{node_id}/text"),
    ("GET", "/api/v1/verbatim/{key}"),
}
OPEN_OPERATIONS = {("GET", "/api/v1/health")}


@pytest.fixture
def scoped(api):
    client, rw_headers, app = api  # the fixture token is rw (default)
    _tid, read_tok = tokens.create_token(app.state.admin_db, "reader", scope="read")
    return client, rw_headers, {"Authorization": f"Bearer {read_tok}"}, app


def _operations(app) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }


def _concrete(path: str) -> str:
    return path.replace("{node_id}", "x").replace("{id}", "x").replace("{key}", "x")


def test_every_operation_is_classified(scoped) -> None:
    _c, _rw, _read, app = scoped
    assert _operations(app) == WRITE_OPERATIONS | READ_OPERATIONS | OPEN_OPERATIONS


def test_read_token_is_refused_on_every_write_operation(scoped) -> None:
    client, _rw, read, _app = scoped
    for method, path in sorted(WRITE_OPERATIONS):
        r = client.request(method, _concrete(path), json={}, headers=read)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
        assert "read-only" in r.json()["detail"]


def test_read_token_is_not_refused_on_read_operations(scoped) -> None:
    client, _rw, read, _app = scoped
    for method, path in sorted(READ_OPERATIONS):
        r = client.request(method, _concrete(path), json={}, headers=read)
        assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}"


def test_rw_token_is_not_refused_on_any_operation(scoped) -> None:
    client, rw, _read, _app = scoped
    for method, path in sorted(WRITE_OPERATIONS | READ_OPERATIONS):
        r = client.request(method, _concrete(path), json={}, headers=rw)
        assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}"


def test_read_token_cannot_change_the_database(scoped) -> None:
    client, rw, read, app = scoped
    before = app.state.store.count()
    client.post("/api/v1/memories", json={"type": "doc", "content": "A memory the reader must not store."}, headers=read)
    client.post("/api/v1/documents", json={"external_handle": {"system": "s", "native_id": "n"},
                                           "blocks": [{"block_type": "paragraph", "text": "Reader must not ingest this."}]},
                headers=read)
    assert app.state.store.count() == before
    mid = client.post("/api/v1/memories", json={"type": "doc", "content": "A memory only the writer stored."},
                      headers=rw).json()["id"]
    assert client.delete(f"/api/v1/memories/{mid}", headers=read).status_code == 403
    assert app.state.store.get_by_uuid(mid) is not None


def test_read_token_can_still_read_what_rw_wrote(scoped) -> None:
    client, rw, read, _app = scoped
    mid = client.post("/api/v1/memories", json={"type": "doc", "content": "Shared knowledge about radiated emissions."},
                      headers=rw).json()["id"]
    r = client.post("/api/v1/query", json={"query": "radiated emissions knowledge"}, headers=read)
    assert r.status_code == 200
    assert mid in r.json()["sources"]


def test_forbidden_call_is_logged_with_the_token(scoped) -> None:
    client, _rw, read, app = scoped
    client.post("/api/v1/links", json={"id_a": "a", "id_b": "b", "edge_type": "references"}, headers=read)
    reader_id = next(t["id"] for t in tokens.list_tokens(app.state.admin_db) if t["name"] == "reader")
    rows = usage.recent(app.state.admin_db, token_id=reader_id, limit=5)
    assert rows[0]["status"] == 403 and rows[0]["path"] == "/api/v1/links"


def test_unauthenticated_write_is_still_401_not_403(scoped) -> None:
    client, _rw, _read, _app = scoped
    assert client.post("/api/v1/memories", json={}).status_code == 401


def test_write_operations_say_they_need_a_read_write_token(scoped) -> None:
    client, rw, _read, _app = scoped
    spec = client.get("/api/v1/openapi.json", headers=rw).json()
    for method, path in WRITE_OPERATIONS:
        description = spec["paths"][path][method.lower()]["description"]
        assert "read/write token" in description, f"{method} {path}"
    for method, path in READ_OPERATIONS:
        description = spec["paths"][path][method.lower()]["description"]
        assert "read/write token" not in description, f"{method} {path}"


def test_read_token_can_open_openapi_docs(scoped) -> None:
    client, _rw, read, _app = scoped
    assert client.get("/api/v1/openapi.json", headers=read).status_code == 200

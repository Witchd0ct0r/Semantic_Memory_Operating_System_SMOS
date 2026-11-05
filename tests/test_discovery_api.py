from __future__ import annotations

from smos.ingest.contract import derive_doc_id


def _doc(client, h, system: str, native_id: str, tags: list[str], text: str, doc_type: str | None = None, **extra):
    body = {"external_handle": {"system": system, "native_id": native_id}, "domain_tags": tags,
            "blocks": [{"block_type": "paragraph", "text": text}], **extra}
    if doc_type:
        body["doc_type"] = doc_type
    r = client.post("/api/v1/documents", json=body, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    return derive_doc_id(system, native_id)


def _seed(client, h) -> list[str]:
    return sorted([
        _doc(client, h, "cb", "R-1", ["norm:A", "topic:x"], "First requirement text about braking.\n", "requirements"),
        _doc(client, h, "cb", "R-2", ["norm:B"], "Second requirement text about steering.\n"),
        _doc(client, h, "eu", "L-1", ["norm:A"], "Regulation text about emissions.\n", "legal"),
    ])


def test_documents_listing_filters_and_pages(api) -> None:
    client, h, _app = api
    ids = _seed(client, h)
    body = client.get("/api/v1/documents", headers=h).json()
    assert body["total"] == 3 and [d["id"] for d in body["documents"]] == ids
    first = next(d for d in body["documents"] if d["external_handle"]["native_id"] == "R-1")
    assert first["external_handle"] == {"system": "cb", "native_id": "R-1", "version": None, "deep_link": None}
    assert first["doc_type"] == "requirements"
    assert first["tags"] == ["norm:A", "topic:x", "doctype:requirements"]
    assert first["segment_count"] >= 1 and first["updated_at"] and first["document_hash"]

    cb = client.get("/api/v1/documents", params={"system": "cb"}, headers=h).json()
    assert cb["total"] == 2 and {d["external_handle"]["native_id"] for d in cb["documents"]} == {"R-1", "R-2"}
    both = client.get("/api/v1/documents", params=[("tag", "norm:A"), ("tag", "topic:x")], headers=h).json()
    assert [d["external_handle"]["native_id"] for d in both["documents"]] == ["R-1"]
    doctype = client.get("/api/v1/documents", params={"tag": "doctype:legal"}, headers=h).json()
    assert [d["external_handle"]["native_id"] for d in doctype["documents"]] == ["L-1"]

    paged: list[str] = []
    for offset in (0, 1, 2, 3):
        page = client.get("/api/v1/documents", params={"limit": 1, "offset": offset}, headers=h).json()
        assert page["total"] == 3
        paged += [d["id"] for d in page["documents"]]
    assert paged == ids


def test_placeholder_documents_not_listed(api) -> None:
    client, h, _app = api
    _doc(client, h, "cb", "SRC", [], "Clause that points to a target ingested later.\n",
         source_links=[{"target_native_id": "NOT-YET", "edge_type": "references"}])
    body = client.get("/api/v1/documents", headers=h).json()
    assert body["total"] == 1 and body["documents"][0]["external_handle"]["native_id"] == "SRC"


def test_tags_listing_counts_and_prefix(api) -> None:
    client, h, _app = api
    _seed(client, h)
    allt = client.get("/api/v1/tags", headers=h).json()
    counts = {t["tag"]: t["count"] for t in allt["tags"]}
    assert counts["norm:A"] >= 2 and counts["norm:B"] >= 1 and counts["doctype:legal"] >= 1
    assert [t["tag"] for t in allt["tags"]] == sorted(counts) and allt["total"] == len(counts)
    norms = client.get("/api/v1/tags", params={"prefix": "norm:"}, headers=h).json()
    assert [t["tag"] for t in norms["tags"]] == ["norm:A", "norm:B"] and norms["total"] == 2


def test_read_token_can_list(api) -> None:
    from smos.admin import tokens
    client, rw, app = api
    _seed(client, rw)
    _tid, tok = tokens.create_token(app.state.admin_db, "reader", scope="read")
    read = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/documents", headers=read).status_code == 200
    assert client.get("/api/v1/tags", headers=read).status_code == 200


def test_listing_limits_and_auth(api) -> None:
    client, h, _app = api
    assert client.get("/api/v1/documents", params={"limit": 501}, headers=h).status_code == 422
    assert client.get("/api/v1/documents", params={"offset": -1}, headers=h).status_code == 422
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/tags").status_code == 401

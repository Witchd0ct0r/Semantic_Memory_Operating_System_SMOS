from __future__ import annotations


def _store(client, headers, content, **extra):
    body = {"type": "doc", "content": content, **extra}
    r = client.post("/api/v1/memories", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _payload(native_id="REQ-1", **extra):
    return {
        "external_handle": {"system": "codebeamer", "native_id": native_id},
        "doc_name": "Radiated emissions requirement",
        "blocks": [
            {"block_type": "heading", "text": "# Radiated emissions\n", "heading_level": 1},
            {"block_type": "paragraph", "text": "The system shall limit radiated emissions below the CISPR 25 limit.\n"},
        ],
        **extra,
    }


def test_post_memory_then_query_returns_it_with_summary(api) -> None:
    client, h, _app = api
    mid = _store(client, h, "PostgreSQL uses MVCC for transaction isolation.",
                 summary="Postgres isolates via MVCC.", summary_source="parser-1")
    r = client.post("/api/v1/query", json={"query": "postgres transaction isolation", "k": 3}, headers=h)
    assert r.status_code == 200
    item = next(x for x in r.json()["results"] if x["id"] == mid)
    assert item["content"] == "PostgreSQL uses MVCC for transaction isolation."
    assert item["summary"] == "Postgres isolates via MVCC."
    assert item["summary_source"] == "parser-1"


def test_query_without_summary_has_null(api) -> None:
    client, h, _app = api
    mid = _store(client, h, "Redis is an in-memory data store used for caching.")
    r = client.post("/api/v1/query", json={"query": "in-memory caching", "k": 3}, headers=h)
    item = next(x for x in r.json()["results"] if x["id"] == mid)
    assert item["summary"] is None


def test_query_too_short_400(api) -> None:
    client, h, _app = api
    assert client.post("/api/v1/query", json={"query": "hi"}, headers=h).status_code == 400


def test_post_memory_too_short_400(api) -> None:
    client, h, _app = api
    r = client.post("/api/v1/memories", json={"type": "doc", "content": "short"}, headers=h)
    assert r.status_code == 400


def test_delete_memory(api) -> None:
    client, h, _app = api
    mid = _store(client, h, "A memory that will be deleted shortly.")
    assert client.delete(f"/api/v1/memories/{mid}", headers=h).status_code == 200
    assert client.delete(f"/api/v1/memories/{mid}", headers=h).status_code == 404


def test_put_node_summary_on_document_node(api) -> None:
    client, h, _app = api
    doc = client.post("/api/v1/documents", json=_payload(), headers=h).json()
    r = client.put(f"/api/v1/nodes/{doc['doc_id']}/summary",
                   json={"summary": "Emission limit requirement.", "source": "parser-2"}, headers=h)
    assert r.status_code == 200
    q = client.post("/api/v1/query", json={"query": "radiated emissions limit CISPR", "k": 5}, headers=h).json()
    assert q["results"]


def test_put_node_summary_unknown_node_404(api) -> None:
    client, h, _app = api
    r = client.put("/api/v1/nodes/does-not-exist/summary", json={"summary": "x"}, headers=h)
    assert r.status_code == 404


def test_ingest_document_with_summary_roundtrip(api) -> None:
    client, h, app = api
    r = client.post("/api/v1/documents", json=_payload(summary="Limits radiated emissions.", summary_source="p1"), headers=h)
    assert r.status_code == 200 and r.json()["status"] == "success"
    doc_id = r.json()["doc_id"]
    assert app.state.store.get_summaries([doc_id])[doc_id]["summary"] == "Limits radiated emissions."
    text = client.get(f"/api/v1/nodes/{doc_id}/text", headers=h).json()
    assert "radiated emissions" in text["text"]


def test_documents_bad_payload_422(api) -> None:
    client, h, _app = api
    r = client.post("/api/v1/documents", json={"blocks": "nope"}, headers=h)
    assert r.status_code == 422


def test_documents_invalid_content_is_quarantined_not_rejected(api) -> None:
    client, h, _app = api
    bad = _payload()
    bad["blocks"] = []
    r = client.post("/api/v1/documents", json=bad, headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "quarantined"


def test_derived_memory_links_sources(api) -> None:
    client, h, _app = api
    a = _store(client, h, "Fragment A about database connection pooling.")
    b = _store(client, h, "Fragment B about database connection timeouts.")
    r = client.post("/api/v1/memories/derived",
                    json={"kind": "compressed", "text": "Pooling and timeouts, combined by the caller.",
                          "source_ids": [a, b]}, headers=h)
    assert r.status_code == 200
    g = client.post("/api/v1/graph/query", json={"term": r.json()["id"], "max_hops": 1}, headers=h).json()
    assert {n["id"] for n in g["nodes"]} >= {a, b}


def test_derived_unknown_source_400(api) -> None:
    client, h, _app = api
    r = client.post("/api/v1/memories/derived",
                    json={"kind": "reflection", "text": "Reflection with a missing source id.",
                          "source_ids": ["nope"]}, headers=h)
    assert r.status_code == 400


def test_recall_exact_strategy(api) -> None:
    client, h, _app = api
    client.post("/api/v1/documents", json=_payload(), headers=h)
    r = client.post("/api/v1/recall", json={"query": "radiated emissions CISPR 25", "strategy": "exact", "k": 3}, headers=h)
    assert r.status_code == 200
    assert r.json()["mode"] in ("extractive", "uncertain")


def test_recall_unknown_strategy_400(api) -> None:
    client, h, _app = api
    r = client.post("/api/v1/recall", json={"query": "anything at all", "strategy": "bogus"}, headers=h)
    assert r.status_code == 400


def test_graph_query_and_link(api) -> None:
    client, h, _app = api
    a = _store(client, h, "The JWTValidator class handles bearer token checks.")
    b = _store(client, h, "Session handling is documented separately here.")
    assert client.post("/api/v1/links", json={"id_a": a, "id_b": b, "edge_type": "related_to"}, headers=h).status_code == 200
    bad = client.post("/api/v1/links", json={"id_a": a, "id_b": b, "edge_type": "not_a_type"}, headers=h)
    assert bad.status_code == 400
    g = client.post("/api/v1/graph/query", json={"term": a, "max_hops": 1}, headers=h).json()
    assert b in {n["id"] for n in g["nodes"]}


def test_requirement_context_route(api) -> None:
    client, h, _app = api
    doc = client.post("/api/v1/documents", json=_payload(domain_tags=["rf-emissions"]), headers=h).json()
    r = client.post("/api/v1/requirement-context",
                    json={"doc_id": doc["doc_id"], "domain_tags": ["rf-emissions"]}, headers=h)
    assert r.status_code == 200
    assert set(r.json()) >= {"query", "linked", "peers", "normative", "coverage", "truncated"}
    missing = client.post("/api/v1/requirement-context", json={"doc_id": "nope"}, headers=h)
    assert missing.status_code == 404


def test_verbatim_roundtrip(api) -> None:
    client, h, _app = api
    key = client.post("/api/v1/verbatim", json={"content": "exact bytes\nline two", "label": "diff"}, headers=h).json()["key"]
    got = client.get(f"/api/v1/verbatim/{key}", headers=h).json()
    assert got["content"] == "exact bytes\nline two"
    assert client.get("/api/v1/verbatim/missing", headers=h).status_code == 404

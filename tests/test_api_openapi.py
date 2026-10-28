from __future__ import annotations


def test_openapi_has_summary_field_and_operation_descriptions(api) -> None:
    client, h, _app = api
    spec = client.get("/api/v1/openapi.json", headers=h).json()
    operations = [
        (path, method, op)
        for path, item in spec["paths"].items()
        for method, op in item.items()
    ]
    assert len(operations) >= 14
    for path, method, op in operations:
        assert op.get("summary"), f"{method} {path} lacks summary"
        assert len(op.get("description", "")) > 30, f"{method} {path} lacks description"
    schemas = spec["components"]["schemas"]
    assert "summary" in schemas["IngestionPayload"]["properties"]
    assert "summary_source" in schemas["IngestionPayload"]["properties"]


def test_swagger_docs_served_with_credentials(api) -> None:
    client, h, _app = api
    r = client.get("/api/v1/docs", headers=h)
    assert r.status_code == 200
    assert "/api/v1/openapi.json" in r.text

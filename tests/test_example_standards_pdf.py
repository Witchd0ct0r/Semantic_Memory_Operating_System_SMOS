from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

pytest.importorskip("pypdf")

from tests.helpers import make_pdf  # noqa: E402

_EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "standards_pdf_parser.py"


def _load():
    spec = importlib.util.spec_from_file_location("standards_pdf_parser", _EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def parser():
    return _load()


def test_to_blocks_headings_and_paragraphs(parser) -> None:
    lines = ["1 Scope", "This part applies", "to all items.", "", "1.1 Purpose", "Text."]
    blocks = parser.to_blocks(lines)
    assert [b["block_type"] for b in blocks] == ["heading", "paragraph", "heading", "paragraph"]
    assert blocks[0]["heading_level"] == 1 and blocks[2]["heading_level"] == 2
    assert blocks[1]["text"] == "This part applies to all items.\n"
    assert all(b["text"].endswith("\n") and b["text"].strip() for b in blocks)


def test_strip_furniture_removes_repeating_header_and_page_numbers(parser) -> None:
    pages = [["ACME STD 1000", f"body line {n}", f"Page {n} of 4"] for n in range(1, 5)]
    assert parser.strip_furniture(pages) == [f"body line {n}" for n in range(1, 5)]
    two = [["ACME STD 1000", "a"], ["ACME STD 1000", "b"]]
    assert parser.strip_furniture(two) == ["ACME STD 1000", "a", "ACME STD 1000", "b"]


def test_find_references_dedupes_and_excludes_own(parser) -> None:
    lines = ["See ISO 26262-6 and ISO 26262-6 again.", "Also iso26262-6, ISO  9001 and ISO 12345 itself."]
    assert parser.find_references(lines, "ISO 12345") == ["ISO 26262-6", "ISO 9001"]


def test_no_text_pdf_raises(parser, tmp_path) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(make_pdf([[], []]))
    with pytest.raises(parser.NoTextError):
        parser.extract_pages(pdf)


def test_build_payload_from_pdf(parser, tmp_path) -> None:
    pdf = tmp_path / "std.pdf"
    pdf.write_bytes(make_pdf([["1 Scope", "Applies to widgets. See ISO 9001.", "2 Terms", "A widget is a thing."]]))
    payload = parser.build_payload(pdf, system="std-pdf", designation="ACME 1000", doc_name="ACME 1000 Widgets",
                                   domain_tags=["widgets"])
    assert payload["external_handle"] == {"system": "std-pdf", "native_id": "ACME 1000"}
    assert payload["doc_type"] == "standard" and payload["domain_tags"] == ["widgets"]
    assert [b["block_type"] for b in payload["blocks"]] == ["heading", "paragraph", "heading", "paragraph"]
    assert payload["source_links"] == [
        {"target_native_id": "ISO 9001", "edge_type": "references", "metadata": {"target_system": "std-pdf"}}
    ]
    assert "summary" not in payload
    json.dumps(payload)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_document_sends_bearer_json_and_surfaces_http_errors(parser) -> None:
    seen = {}

    def ok(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return _Response(b'{"status": "success", "doc_id": "abc"}')

    result = parser.post_document("http://smos:8765/", "tok", {"a": 1}, opener=ok)
    assert result == {"status": "success", "doc_id": "abc"}
    assert seen == {"url": "http://smos:8765/api/v1/documents", "auth": "Bearer tok", "body": {"a": 1}}

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"detail": "this token is read-only"}'))

    with pytest.raises(parser.SmosError, match="403"):
        parser.post_document("http://smos:8765", "tok", {}, opener=refuse)


def test_dry_run_posts_nothing(parser, tmp_path, monkeypatch, capsys) -> None:
    pdf = tmp_path / "std.pdf"
    pdf.write_bytes(make_pdf([["1 Scope", "Text here."]]))

    def fail(*args, **kwargs):
        raise AssertionError("dry run must not post")

    monkeypatch.setattr(parser, "OPENER", fail)
    code = parser.main(["--pdf", str(pdf), "--system", "s", "--designation", "D 1", "--name", "D", "--dry-run"])
    assert code == 0
    assert "2 blocks" in capsys.readouterr().out


# --- against a real SMOS app (the behavior the guide documents) -------------------------------------

_PAGES = [
    ["1 Scope", "Température ± 0,5 mm à 20 °C – hôtel.", "", "1.1 Purpose"],
    ["2 Terms", "A widget is a small mechanical thing used for testing.", "", "2.1 Widget limits",
     "The maximum permitted widget deflection shall not exceed two millimetres."],
]


def _payload(parser, tmp_path, pages=None, designation="ACME 1000"):
    pdf = tmp_path / "std.pdf"
    pdf.write_bytes(make_pdf(pages or _PAGES))
    return parser.build_payload(pdf, system="std-pdf", designation=designation, doc_name=designation,
                                domain_tags=["widgets"])


def test_ingest_and_exact_reconstruction(parser, api, tmp_path) -> None:
    client, headers, _app = api
    payload = _payload(parser, tmp_path)
    body = client.post("/api/v1/documents", json=payload, headers=headers).json()
    assert body["status"] == "success"
    text = client.get(f"/api/v1/nodes/{body['doc_id']}/text", headers=headers).json()
    assert text["text"] == "".join(b["text"] for b in payload["blocks"])
    assert "Température ± 0,5 mm à 20 °C – hôtel." in text["text"]


def test_query_returns_the_matching_clause(parser, api, tmp_path) -> None:
    client, headers, _app = api
    doc_id = client.post("/api/v1/documents", json=_payload(parser, tmp_path), headers=headers).json()["doc_id"]
    results = client.post("/api/v1/query", json={"query": "maximum permitted widget deflection", "k": 3},
                          headers=headers).json()["results"]
    assert "deflection shall not exceed two millimetres" in results[0]["content"]
    assert results[0]["metadata"]["doc_id"] == doc_id


def test_reingest_is_idempotent_and_changed_pdf_replaces(parser, api, tmp_path) -> None:
    client, headers, _app = api
    payload = _payload(parser, tmp_path)
    first = client.post("/api/v1/documents", json=payload, headers=headers).json()
    second = client.post("/api/v1/documents", json=payload, headers=headers).json()
    assert second["doc_id"] == first["doc_id"] and second["segment_count"] == first["segment_count"]
    changed = [list(p) for p in _PAGES]
    changed[1][4] = "The maximum permitted widget deflection shall not exceed five furlongs."
    third = client.post("/api/v1/documents", json=_payload(parser, tmp_path, changed), headers=headers).json()
    assert third["doc_id"] == first["doc_id"]
    results = client.post("/api/v1/query", json={"query": "maximum permitted widget deflection", "k": 10},
                          headers=headers).json()["results"]
    texts = " ".join(r["content"] for r in results)
    assert "five furlongs" in texts and "two millimetres" not in texts


def test_read_only_token_cannot_ingest(parser, api, tmp_path) -> None:
    from smos.admin import tokens

    client, _headers, app = api
    _id, read_token = tokens.create_token(app.state.admin_db, "ro", scope="read")
    response = client.post("/api/v1/documents", json=_payload(parser, tmp_path),
                           headers={"Authorization": f"Bearer {read_token}"})
    assert response.status_code == 403
    assert response.json() == {"detail": "this token is read-only"}


def test_link_to_an_uningested_standard(parser, api, tmp_path) -> None:
    """Pins documented behavior: a link may name a document that is not stored (yet)."""
    client, headers, _app = api
    pages = [["1 Scope", "Applies together with ISO 9001 only."]]
    payload = _payload(parser, tmp_path, pages)
    assert payload["source_links"]
    response = client.post("/api/v1/documents", json=payload, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "success"


def test_heading_without_body_and_body_before_first_heading(parser, api, tmp_path) -> None:
    client, headers, _app = api
    only_heading = _payload(parser, tmp_path, [["1 Scope"]], designation="H 1")
    body_first = _payload(parser, tmp_path, [["Some preface text.", "", "1 Scope", "Body."]], designation="H 2")
    for payload in (only_heading, body_first):
        assert client.post("/api/v1/documents", json=payload, headers=headers).json()["status"] == "success"


def test_blocks_without_trailing_newline_run_together(api) -> None:
    client, headers, _app = api
    payload = {"external_handle": {"system": "t", "native_id": "n"},
               "blocks": [{"block_type": "paragraph", "text": "First line."},
                          {"block_type": "paragraph", "text": "Second line."}]}
    doc_id = client.post("/api/v1/documents", json=payload, headers=headers).json()["doc_id"]
    assert client.get(f"/api/v1/nodes/{doc_id}/text", headers=headers).json()["text"] == "First line.Second line."

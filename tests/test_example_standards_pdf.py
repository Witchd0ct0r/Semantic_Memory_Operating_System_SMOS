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

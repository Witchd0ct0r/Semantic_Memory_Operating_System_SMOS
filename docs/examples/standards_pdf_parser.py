"""Example SMOS parser: a text PDF of a standard -> ingestion contract -> POST /api/v1/documents.

This is a teaching example, not a general PDF parser. It handles PDFs that contain real text (not scans),
treats numbered lines such as "5.2.1 Title" as headings, and declares references to other standards
("ISO 26262-6") as links, because SMOS does not detect references in text over the API.

    pip install -r docs/examples/requirements.txt
    export SMOS_TOKEN=...            # a read/write token
    python docs/examples/standards_pdf_parser.py --pdf std.pdf --system std-pdf \\
        --designation "ACME 1000" --name "ACME 1000 Widgets" --tag widgets --url http://localhost:8765
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

OPENER = urllib.request.urlopen

_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d+(?:\s+of\s+\d+)?\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\s+([A-Z].{0,99})$")
_REFERENCE = re.compile(r"\b(?:ISO|IEC|EN|DIN)\s{0,3}\d{3,6}(?:-\d+)?\b")


class NoTextError(RuntimeError):
    """The PDF has no extractable text (probably a scan)."""


class SmosError(RuntimeError):
    """SMOS answered with an HTTP error."""


def extract_pages(pdf_path: Path) -> list[list[str]]:
    from pypdf import PdfReader

    pages = [(page.extract_text() or "").splitlines() for page in PdfReader(str(pdf_path)).pages]
    if not any(line.strip() for page in pages for line in page):
        raise NoTextError(f"{pdf_path} has no extractable text; it may be a scan (this example does no OCR)")
    return pages


def strip_furniture(pages: list[list[str]]) -> list[str]:
    """Flatten the pages, dropping bare page numbers and, for documents of 3+ pages, any line that
    appears on more than half the pages (running headers and footers)."""
    repeated: set[str] = set()
    if len(pages) >= 3:
        counts: dict[str, int] = {}
        for page in pages:
            for line in {l.strip() for l in page if l.strip()}:
                counts[line] = counts.get(line, 0) + 1
        repeated = {line for line, n in counts.items() if n > len(pages) / 2}
    return [line for page in pages for line in page
            if not _PAGE_NUMBER.match(line) and line.strip() not in repeated]


def to_blocks(lines: list[str]) -> list[dict]:
    """Headings and paragraphs. SMOS joins block texts with nothing in between, so every block ends
    with its own newline."""
    blocks: list[dict] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append({"block_type": "paragraph", "text": " ".join(paragraph) + "\n"})
            paragraph.clear()

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            continue
        match = _HEADING.match(line)
        if match and not line.endswith("."):
            flush()
            level = min(match.group(1).count(".") + 1, 6)
            blocks.append({"block_type": "heading", "heading_level": level, "text": line + "\n"})
        else:
            paragraph.append(line)
    flush()
    return blocks


def find_references(lines: list[str], own_designation: str) -> list[str]:
    own = " ".join(own_designation.split())
    found: list[str] = []
    for line in lines:
        for match in _REFERENCE.findall(line):
            designation = " ".join(match.split())
            if designation != own and designation not in found:
                found.append(designation)
    return found


def build_payload(
    pdf_path: Path,
    system: str,
    designation: str,
    doc_name: str,
    doc_type: str = "standard",
    domain_tags: Optional[list[str]] = None,
    summary: Optional[str] = None,
) -> dict:
    lines = strip_furniture(extract_pages(Path(pdf_path)))
    payload: dict = {
        "external_handle": {"system": system, "native_id": designation},
        "doc_name": doc_name,
        "blocks": to_blocks(lines),
        "doc_type": doc_type,
        "domain_tags": list(domain_tags or []),
        "source_links": [
            {"target_native_id": ref, "edge_type": "references", "metadata": {"target_system": system}}
            for ref in find_references(lines, designation)
        ],
    }
    if summary:
        payload["summary"] = summary
        payload["summary_source"] = "standards_pdf_parser 1"
    return payload


def post_document(base_url: str, token: str, payload: dict, opener=urllib.request.urlopen) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/v1/documents",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise SmosError(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')}") from error


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a standards PDF into SMOS")
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--system", required=True, help="source system name, e.g. std-pdf")
    parser.add_argument("--designation", required=True, help="the standard's own id, e.g. 'ACME 1000'")
    parser.add_argument("--name", required=True)
    parser.add_argument("--tag", action="append", default=[], help="domain tag (repeatable, no commas)")
    parser.add_argument("--summary")
    parser.add_argument("--url", default="http://localhost:8765")
    parser.add_argument("--token-env", default="SMOS_TOKEN")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = build_payload(args.pdf, args.system, args.designation, args.name,
                                domain_tags=args.tag, summary=args.summary)
    except NoTextError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"{len(payload['blocks'])} blocks, {len(payload['source_links'])} links")
    if args.dry_run:
        return 0
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"error: set {args.token_env} to a read/write token", file=sys.stderr)
        return 1
    try:
        result = post_document(args.url, token, payload, opener=OPENER)
    except SmosError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if result.get("status") == "quarantined":
        print("quarantined:", *result.get("errors", []), sep="\n  ")
        return 2
    print(f"status={result['status']} doc_id={result['doc_id']} segments={result['segment_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

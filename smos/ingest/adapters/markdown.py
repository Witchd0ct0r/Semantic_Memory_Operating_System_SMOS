from __future__ import annotations

import re
from typing import Optional

_HEADING_RE = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_EXTERNAL_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


class MarkdownAdapter:
    """Minimal domain adapter proving the DomainAdapter interface end-to-end.

    Structural grammar: split on markdown ATX headings (# through ######).
    Extraction rules: internal markdown links (`[text](#anchor)`,
    `[text](other-doc)`) and wiki-links (`[[Target]]`) — real cross-references
    a markdown corpus actually contains. External URLs are skipped (not
    references into this corpus). Every extraction call also emits a
    "self-topic" triple `(heading, heading, "references")` for the nearest
    heading in the given text, so a section that *defines* a term links to
    that term's concept node even when it contains no outgoing link itself —
    without this, a separate section that only *links to* the defining
    section's anchor would have nothing to connect to. No LLM boundary
    prompt yet.
    """

    name = "markdown"
    edge_types: frozenset[str] = frozenset({"references"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return []
        spans = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            spans.append((start, end))
        return spans

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        """(source_name, target_name, "references") triples. source_name is
        the *last* (nearest-to-the-end) heading found in `text` — callers
        that want "the heading nearest this specific span" pass a text
        window ending at that span (see `_wire_references` in
        `smos/ingest/segmenter.py`, which prepends a lookback window so a
        body block that arrived as a separate ContentBlock from its own
        heading still resolves the right source_name)."""
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return []
        source_name = matches[-1].group(0).lstrip("#").strip()
        if not source_name:
            return []

        triples: list[tuple[str, str, str]] = [(source_name, source_name, "references")]
        seen: set[str] = {source_name.lower()}

        for m in _LINK_RE.finditer(text):
            target = m.group(2).strip()
            if not target or _EXTERNAL_URL_RE.match(target):
                continue
            target_name = target[1:].replace("-", " ").strip() if target.startswith("#") else target
            if not target_name or target_name.lower() in seen:
                continue
            seen.add(target_name.lower())
            triples.append((source_name, target_name, "references"))

        for m in _WIKILINK_RE.finditer(text):
            target_name = m.group(1).strip()
            if not target_name or target_name.lower() in seen:
                continue
            seen.add(target_name.lower())
            triples.append((source_name, target_name, "references"))

        return triples

    def llm_boundary_prompt(self) -> Optional[str]:
        return None

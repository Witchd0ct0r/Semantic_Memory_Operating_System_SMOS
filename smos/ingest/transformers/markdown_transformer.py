from __future__ import annotations

import re
from typing import Optional

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload

_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|.*\|[ \t]*$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")


def _classify_line(line: str, current_run_type: Optional[str]) -> str:
    """Classify one line (newline included) for _split_body_blocks. A line
    indented under an in-progress "list" run is treated as a wrapped
    continuation of that list item rather than its own paragraph, so a
    multi-line bullet stays inside one "list" block."""
    stripped = line.rstrip("\r\n")
    if not stripped.strip():
        return "blank"
    if _TABLE_ROW_RE.match(stripped):
        return "table"
    if _LIST_ITEM_RE.match(stripped):
        return "list"
    if current_run_type == "list" and stripped[:1] in (" ", "\t"):
        return "list"
    return "paragraph"


def _split_body_blocks(text: str) -> list[ContentBlock]:
    """Split a heading-free span into typed ContentBlocks: a contiguous run
    of markdown table rows (lines starting and ending with `|`) becomes one
    "table" block, a contiguous run of list items (`-`/`*`/`+`/`N.`/`N)`
    markers, plus their indented wrapped-continuation lines) becomes one
    "list" block, everything else stays "paragraph" — matching the prior
    single-paragraph behavior for spans with no lists or tables. Not a full
    markdown parser: code fences, blockquotes, and nested/mixed list styles
    all still fall through to "paragraph", same as before this function
    existed.

    Blank lines are folded into whichever run precedes them (never given
    their own block), and a span that opens with blank lines before any real
    content (e.g. the leftover newline from the preceding heading's own
    regex match) has that leading whitespace folded forward into the first
    real block. Both rules exist so blocks_to_full_text's byte-exact
    reconstruction never has to drop a whitespace-only block — validate_payload's
    "block text must not be blank" rule would otherwise quarantine a payload
    with a real, structurally correct list or table simply because a blank
    line landed in its own block."""
    if not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    runs: list[tuple[str, str]] = []

    for line in lines:
        current_type = runs[-1][0] if runs else None
        kind = _classify_line(line, current_type)
        if kind == "blank" and runs:
            prev_type, prev_text = runs[-1]
            runs[-1] = (prev_type, prev_text + line)
        elif runs and runs[-1][0] == kind:
            prev_type, prev_text = runs[-1]
            runs[-1] = (prev_type, prev_text + line)
        else:
            runs.append((kind if kind != "blank" else "paragraph", line))

    blocks: list[ContentBlock] = []
    carry = ""
    for block_type, run_text in runs:
        combined = carry + run_text
        if not combined.strip():
            carry = combined
            continue
        blocks.append(ContentBlock(block_type=block_type, text=combined))
        carry = ""

    return blocks


def markdown_to_payload(
    text: str, system: str, native_id: str, doc_name: Optional[str] = None
) -> IngestionPayload:
    """Parse ATX headings plus typed list/table/paragraph body blocks into
    ContentBlocks and wrap in a valid IngestionPayload. Reference
    implementation proving the client side of the ingestion contract — see
    _split_body_blocks for exactly which markdown shapes get their own
    block_type and which still fall back to "paragraph"."""
    matches = list(_HEADING_RE.finditer(text))
    blocks: list[ContentBlock] = []

    if not matches:
        blocks.extend(_split_body_blocks(text))
        return IngestionPayload(
            external_handle=ExternalHandle(system=system, native_id=native_id),
            doc_name=doc_name,
            blocks=blocks,
        )

    if matches[0].start() > 0:
        blocks.extend(_split_body_blocks(text[: matches[0].start()]))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        blocks.append(ContentBlock(block_type="heading", text=m.group(0), heading_level=level))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.extend(_split_body_blocks(text[body_start:body_end]))

    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=doc_name,
        blocks=blocks,
    )

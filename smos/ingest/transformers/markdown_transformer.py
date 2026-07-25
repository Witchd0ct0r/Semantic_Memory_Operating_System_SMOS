from __future__ import annotations

import re
from typing import Optional

from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload

_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$", re.MULTILINE)


def markdown_to_payload(
    text: str, system: str, native_id: str, doc_name: Optional[str] = None
) -> IngestionPayload:
    """Parse ATX headings + paragraphs into typed ContentBlocks and wrap in a
    valid IngestionPayload. Reference implementation proving the client side
    of the ingestion contract; a minimal example, not a full markdown parser
    (no list/table block typing — everything between headings becomes one
    paragraph block)."""
    matches = list(_HEADING_RE.finditer(text))
    blocks: list[ContentBlock] = []

    if not matches:
        if text.strip():
            blocks.append(ContentBlock(block_type="paragraph", text=text))
        return IngestionPayload(
            external_handle=ExternalHandle(system=system, native_id=native_id),
            doc_name=doc_name,
            blocks=blocks,
        )

    if matches[0].start() > 0:
        lead = text[: matches[0].start()]
        if lead.strip():
            blocks.append(ContentBlock(block_type="paragraph", text=lead))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        blocks.append(ContentBlock(block_type="heading", text=m.group(0), heading_level=level))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        if body.strip():
            blocks.append(ContentBlock(block_type="paragraph", text=body))

    return IngestionPayload(
        external_handle=ExternalHandle(system=system, native_id=native_id),
        doc_name=doc_name,
        blocks=blocks,
    )

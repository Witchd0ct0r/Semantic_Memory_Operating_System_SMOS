from __future__ import annotations

import re
from typing import Optional

_HEADING_RE = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


class MarkdownAdapter:
    """Minimal domain adapter proving the DomainAdapter interface end-to-end.

    Structural grammar: split on markdown ATX headings (# through ######).
    No cross-reference extraction or LLM boundary prompt yet — those land
    with later, domain-specific adapters (Pillar F).
    """

    name = "markdown"
    edge_types: frozenset[str] = frozenset()

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
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
